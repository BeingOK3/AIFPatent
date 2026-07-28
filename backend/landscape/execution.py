from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Iterable
from typing import Any, Protocol

from idea.merge import MergedHit
from idea.merge import normalize_publication_number
from idea.model_client import StructuredModelClient
from idea.providers.base import FetchRequest, FetchedDocument, ProviderResult, ProviderRunner, SearchHit, SearchProvider

from .analysis import LandscapeAnalysisService
from .clustering import LandscapeClusteringError, LandscapeClusteringService
from .company_assignment import CompanyAssignmentResult, assign_companies
from .database import LandscapeDatabase
from .planning import (
    CompetitorAliasService,
    TechnicalDirectionService,
    build_deterministic_query_plan,
    fallback_alias_plan,
    scope_with_alias_plan,
)
from .reporting import LandscapeReportService, build_report
from .schemas import LandscapeQueryPlan, LandscapeScope
from .search import (
    CompanyPatentCount,
    LandscapeCandidateLimitExceededError,
    candidate_company,
    execute_provider_queries,
    exclusion_reason,
    strict_filter_and_select,
    weighted_analysis_selection,
)
from .store import LandscapeRunStore
from .workflow import LandscapeWorkflowStep


class LandscapeCandidateRepository(Protocol):
    def put_candidates(
        self, run_id: str, candidates: list[dict[str, Any]]
    ) -> list[dict[str, Any]]: ...


class LandscapeCompanyRepository(Protocol):
    def put_company_assignments(
        self,
        run_id: str,
        *,
        scope: LandscapeScope,
        result: CompanyAssignmentResult,
    ) -> CompanyAssignmentResult: ...


class LandscapeExecutionService:
    def __init__(
        self,
        *,
        database: LandscapeDatabase,
        store: LandscapeRunStore,
        model: StructuredModelClient,
        providers: list[SearchProvider],
        provider_timeout_seconds: dict[str, float],
        analysis_concurrency: int,
        report_service: LandscapeReportService,
        candidate_repository: "LandscapeCandidateRepository | None" = None,
        company_repository: "LandscapeCompanyRepository | None" = None,
    ):
        self.database = database
        self.store = store
        self.providers = providers
        self.provider_timeout_seconds = provider_timeout_seconds
        self.runner = ProviderRunner()
        self.analysis = LandscapeAnalysisService(
            model, database, concurrency=analysis_concurrency
        )
        self.clustering = LandscapeClusteringService(model)
        self.aliases = CompetitorAliasService(model)
        self.directions = TechnicalDirectionService(model)
        self.report_service = report_service
        self.candidate_repository = candidate_repository
        self.company_repository = company_repository
        self.documents: dict[str, dict[str, FetchedDocument]] = {}
        self.prefetched_documents: dict[str, dict[str, FetchedDocument]] = {}
        self.enrichment_stats: dict[str, dict[str, int]] = {}
        self.cluster_failures: dict[str, str] = {}

    async def handle_step(
        self, run_id: str, step: LandscapeWorkflowStep, attempt: int
    ) -> dict[str, Any]:
        handlers = {
            LandscapeWorkflowStep.VALIDATE_SCOPE: self.validate_scope,
            LandscapeWorkflowStep.PLAN_SEARCH: self.plan_search,
            LandscapeWorkflowStep.SEARCH_PUBLICATIONS: self.search_publications,
            LandscapeWorkflowStep.FILTER_AND_SELECT: self.filter_and_select,
            LandscapeWorkflowStep.FETCH_DETAILS: self.fetch_details,
            LandscapeWorkflowStep.ANALYZE_PATENTS: self.analyze_patents,
            LandscapeWorkflowStep.CLUSTER_PATENTS: self.cluster_patents,
            LandscapeWorkflowStep.BUILD_REPORT: self.build_report,
        }
        return await handlers[step](run_id)

    async def validate_scope(self, run_id: str) -> dict[str, Any]:
        scope = self.scope(run_id)
        return {"valid": True, "mode": scope.mode.value, "window_days": (scope.publication_end - scope.publication_start).days + 1}

    async def plan_search(self, run_id: str) -> dict[str, Any]:
        scope = self.scope(run_id)
        direction_expansion = (
            await self.directions.expand(scope.technology_direction)
            if scope.technology_direction
            else None
        )
        alias_error = None
        if scope.competitors:
            try:
                alias_plan = await self.aliases.resolve(scope.competitors)
            except Exception as exc:
                alias_plan = fallback_alias_plan(scope.competitors)
                alias_error = f"{type(exc).__name__}: {str(exc)[:500]}"
            search_scope = scope_with_alias_plan(scope, alias_plan)
        else:
            alias_plan = None
            search_scope = scope
        plan = build_deterministic_query_plan(search_scope, direction_expansion)
        self.database.put_queries(
            run_id,
            [
                {
                    "query_id": self.query_key(run_id, index),
                    "query_text": query.query_text,
                    "language": query.language,
                    "rationale": query.rationale,
                }
                for index, query in enumerate(plan.queries, start=1)
            ],
        )
        return {
            "plan": plan.model_dump(mode="json"),
            "competitor_aliases": alias_plan.model_dump(mode="json")["competitors"] if alias_plan else [],
            "technical_direction_expansion": (
                direction_expansion.model_dump(mode="json")
                if direction_expansion
                else None
            ),
            "alias_resolution_error": alias_error,
        }

    async def search_publications(self, run_id: str) -> dict[str, Any]:
        scope = self.search_scope(run_id)
        plan = self.load_plan(run_id)
        results = await execute_provider_queries(
            scope=scope,
            plan=plan,
            providers=self.providers,
            runner=self.runner,
            timeout_seconds=self.provider_timeout_seconds,
        )
        return {"results": [result.model_dump(mode="json") for result in results]}

    async def filter_and_select(self, run_id: str) -> dict[str, Any]:
        # The persisted run scope is the authority for hard assignee filtering and
        # company statistics. Model-inferred aliases are search hints only.
        scope = self.scope(run_id)
        raw = self.database.get_stage_result(run_id, LandscapeWorkflowStep.SEARCH_PUBLICATIONS.value)["value"]
        results = [ProviderResult.model_validate(item) for item in raw["results"]]
        results = await self._enrich_missing_dates(run_id, results)
        batches = [(result.request_id, result.hits) for result in results if result.succeeded]
        statuses = {f"{result.request_id}:{result.provider}": result.status.value for result in results}
        result = strict_filter_and_select(
            batches,
            scope=scope,
            provider_statuses=statuses,
            direction_terms=self.load_plan(run_id).direction_terms,
        )
        for provider_result in results:
            for hit in provider_result.hits:
                reason = exclusion_reason(hit, scope)
                decision = "ELIGIBLE" if reason is None else "EXCLUDED"
                hit_id = _hit_id(run_id, provider_result.request_id, hit)
                self.database.put_hit(
                    run_id,
                    hit_id=hit_id,
                    query_id=self.query_key(run_id, int(provider_result.request_id.split("-")[-1])),
                    provider=hit.provider,
                    publication_number=hit.publication_number,
                    application_number=hit.application_number,
                    publication_date=hit.publication_date,
                    assignee=hit.assignee,
                    normalized_key=normalize_publication_number(hit.publication_number),
                    decision=decision,
                    exclusion_reason=reason.value if reason else None,
                    raw=hit.model_dump(mode="json"),
                )
        if self.candidate_repository is not None:
            ranking_by_publication = {
                item.publication_number: item
                for item in result.ranking
            }
            self.candidate_repository.put_candidates(
                run_id,
                [
                    {
                        "document_id": document_id(candidate.publication_number or ""),
                        "publication_number": candidate.publication_number,
                        "normalized_key": candidate.publication_number,
                        "rank": rank,
                        "decision": "ELIGIBLE",
                        "metadata": {
                            "title": candidate.title,
                            "application_number": candidate.application_number,
                            "family_id": candidate.family_id,
                            "priority_date": candidate.priority_date,
                            "filing_date": candidate.filing_date,
                            "publication_date": candidate.publication_date,
                            "assignee": candidate.assignee,
                            "found_by": sorted(candidate.found_by),
                            "query_ids": sorted(candidate.query_ids),
                            "ranking": (
                                {
                                    **ranking_by_publication[
                                        candidate.publication_number
                                    ].model_dump(mode="json"),
                                    "reasons": sorted(
                                        ranking_by_publication[
                                            candidate.publication_number
                                        ].reasons
                                    ),
                                }
                                if candidate.publication_number in ranking_by_publication
                                else None
                            ),
                        },
                    }
                    for rank, candidate in enumerate(result.candidates, start=1)
                ],
            )
        company_result = assign_companies(
            result.candidates,
            scope,
            user_confirmed_competitors=(
                scope.competitors if scope.competitors else None
            ),
        )
        if self.company_repository is not None:
            self.company_repository.put_company_assignments(
                run_id,
                scope=scope,
                result=company_result,
            )
        if result.coverage.candidate_limit_exceeded:
            raise LandscapeCandidateLimitExceededError(
                unique_candidate_count=result.coverage.unique_candidate_count,
                candidate_limit=scope.budget.candidate_limit,
            )
        return {
            "result": result.model_dump(mode="json"),
            "enrichment": self.enrichment_stats.get(run_id, {}),
            "company_assignment": {
                "company_count": len(company_result.companies),
                "assignment_count": len(company_result.assignments),
                "company_ids": [
                    company.company_id for company in company_result.companies
                ],
            },
        }

    async def fetch_details(self, run_id: str) -> dict[str, Any]:
        # Company balancing and attribution use only user-confirmed names/aliases.
        scope = self.scope(run_id)
        filtered = self.database.get_stage_result(
            run_id, LandscapeWorkflowStep.FILTER_AND_SELECT.value
        )["value"]["result"]
        company_counts = [
            CompanyPatentCount.model_validate(item)
            for item in filtered.get("coverage", {}).get("company_patent_counts", [])
        ]
        candidates = self.selected_hits(run_id)
        ordered = weighted_analysis_selection(
            candidates,
            scope=scope,
            company_patent_counts=company_counts,
        )
        target = min(scope.budget.analysis_limit, len(ordered))
        companies = {
            candidate_company(candidate, scope) for candidate in candidates
        }
        docs: dict[str, FetchedDocument] = {}
        failures: dict[str, str] = {}
        attempted: list[MergedHit] = []
        batch = ordered[:target]
        remaining = ordered[target:]
        while len(docs) < target and batch:
            attempted.extend(batch)
            fetched, batch_failures = await self._fetch_documents(run_id, batch)
            docs.update(fetched)
            failures.update(batch_failures)
            needed = target - len(docs)
            failed_companies = [
                candidate_company(hit, scope)
                for hit in batch
                if (hit.publication_number or hit.title) in batch_failures
            ]
            next_batch: list[MergedHit] = []
            for company in failed_companies:
                replacement = next(
                    (
                        hit
                        for hit in remaining
                        if candidate_company(hit, scope) == company
                    ),
                    None,
                )
                if replacement is not None:
                    remaining.remove(replacement)
                    next_batch.append(replacement)
            while len(next_batch) < needed and remaining:
                next_batch.append(remaining.pop(0))
            batch = next_batch[:needed]
        self.documents[run_id] = docs
        for publication, document in docs.items():
            self.database.put_document(
                run_id,
                document_id=document_id(publication),
                publication_number=publication,
                status="FETCHED",
                metadata=_document_metadata(document),
            )
        for publication, error in failures.items():
            self.database.put_document(
                run_id,
                document_id=document_id(publication),
                publication_number=publication,
                status="FAILED",
                metadata={"publication_number": publication},
                error_code="FETCH_FAILED",
                error_message=error,
            )
        return {
            "target_count": target,
            "selected_publications": [
                hit.publication_number for hit in ordered[:target] if hit.publication_number
            ],
            "attempted_publications": [
                hit.publication_number for hit in attempted if hit.publication_number
            ],
            "fetched_publications": sorted(docs),
            "backfilled_count": max(0, len(attempted) - target),
            "company_coverage_complete": target >= len(companies),
            "company_count": len(companies),
            "failures": failures,
        }

    async def analyze_patents(self, run_id: str) -> dict[str, Any]:
        scope = self.scope(run_id)
        docs = self.documents.get(run_id)
        if docs is None:
            fetch_result = self.database.get_stage_result(
                run_id, LandscapeWorkflowStep.FETCH_DETAILS.value
            )["value"]
            fetched_publications = set(fetch_result.get("fetched_publications", []))
            selected = [
                hit
                for hit in self.selected_hits(run_id)
                if hit.publication_number in fetched_publications
            ][: scope.budget.analysis_limit]
            docs, _ = await self._fetch_documents(run_id, selected)
            self.documents[run_id] = docs
        plan = self.load_plan(run_id)
        direction_terms = plan.direction_terms or ([scope.technology_direction] if scope.technology_direction else [])
        analyses, failures = await self.analysis.analyze_many(
            run_id=run_id,
            documents=[(document_id(publication), document) for publication, document in docs.items()],
            direction_terms=direction_terms,
        )
        for publication in analyses:
            self.database.put_document(
                run_id,
                document_id=document_id(publication),
                publication_number=publication,
                status="ANALYZED",
                metadata=_document_metadata(docs[publication]),
            )
        return {"analyses": {key: value.model_dump(mode="json") for key, value in analyses.items()}, "failures": failures}

    async def cluster_patents(self, run_id: str) -> dict[str, Any]:
        raw = self.database.get_stage_result(run_id, LandscapeWorkflowStep.ANALYZE_PATENTS.value)["value"]
        from .schemas import LandscapePatentAnalysis

        analyses = {key: LandscapePatentAnalysis.model_validate(value) for key, value in raw["analyses"].items()}
        if not analyses:
            self.cluster_failures[run_id] = "没有成功精读文献，无法形成技术聚类。"
            return {"clusters": [], "failure": self.cluster_failures[run_id]}
        docs = self.documents.get(run_id, {})
        metadata = {publication: {"title": document.title, "abstract": document.abstract_text} for publication, document in docs.items()}
        try:
            plan = await self.clustering.cluster(analyses, metadata)
        except LandscapeClusteringError as exc:
            self.cluster_failures[run_id] = str(exc)
            return {"clusters": [], "failure": str(exc)}
        clusters = plan.model_dump(mode="json")["clusters"]
        self.database.put_clusters(run_id, clusters, {publication: document_id(publication) for publication in analyses})
        return {"clusters": clusters}

    async def build_report(self, run_id: str) -> dict[str, Any]:
        from .schemas import LandscapeClusterPlan, LandscapePatentAnalysis

        run = self.database.get_run(run_id)
        coverage = self.database.get_stage_result(run_id, LandscapeWorkflowStep.FILTER_AND_SELECT.value)["value"]["result"]["coverage"]
        analysis_raw = self.database.get_stage_result(run_id, LandscapeWorkflowStep.ANALYZE_PATENTS.value)["value"]
        analyses = {key: LandscapePatentAnalysis.model_validate(value) for key, value in analysis_raw["analyses"].items()}
        cluster_raw = self.database.get_stage_result(run_id, LandscapeWorkflowStep.CLUSTER_PATENTS.value)["value"]
        clusters = LandscapeClusterPlan.model_validate({"clusters": cluster_raw["clusters"]}) if cluster_raw["clusters"] else None
        limitations = self.collect_limitations(run_id)
        report = build_report(
            run=run,
            coverage=coverage,
            documents=self.documents.get(run_id, {}),
            analyses=analyses,
            clusters=clusters,
            failures=analysis_raw["failures"],
            limitations=limitations,
            searched_competitor_aliases=self.report_alias_output(run_id),
            technical_direction_expansion=self.direction_output(run_id),
        )
        self.report_service.save(run_id, report)
        return {"report": report, "manifest": "manifest.json"}

    def collect_limitations(self, run_id: str) -> list[dict[str, Any]]:
        limitations: list[dict[str, Any]] = []
        try:
            filtered = self.database.get_stage_result(run_id, LandscapeWorkflowStep.FILTER_AND_SELECT.value)["value"]["result"]
            coverage = filtered["coverage"]
            limitations.extend(coverage_limitations(coverage))
        except KeyError:
            pass
        try:
            fetch = self.database.get_stage_result(run_id, LandscapeWorkflowStep.FETCH_DETAILS.value)["value"]
            if fetch["failures"]:
                limitations.append({"code": "FETCH_FAILURE", "message": f"{len(fetch['failures'])} 件专利全文抓取失败。"})
        except KeyError:
            pass
        try:
            analysis = self.database.get_stage_result(run_id, LandscapeWorkflowStep.ANALYZE_PATENTS.value)["value"]
            if analysis["failures"]:
                limitations.append({"code": "ANALYSIS_FAILURE", "message": f"{len(analysis['failures'])} 件专利精读失败。"})
        except KeyError:
            pass
        if run_id in self.cluster_failures:
            limitations.append({"code": "CLUSTER_FAILURE", "message": self.cluster_failures[run_id]})
        try:
            plan = self.database.get_stage_result(run_id, LandscapeWorkflowStep.PLAN_SEARCH.value)["value"]
            if plan.get("alias_resolution_error"):
                limitations.append({"code": "COMPETITOR_ALIAS_FALLBACK", "message": "友商别名模型解析失败，本次仅使用用户输入的主名称。"})
        except KeyError:
            pass
        return limitations

    def scope(self, run_id: str) -> LandscapeScope:
        return LandscapeScope.model_validate(self.database.get_run(run_id)["scope_json"])

    def load_plan(self, run_id: str) -> LandscapeQueryPlan:
        raw = self.database.get_stage_result(run_id, LandscapeWorkflowStep.PLAN_SEARCH.value)["value"]
        return LandscapeQueryPlan.model_validate(raw["plan"])

    def alias_output(self, run_id: str) -> list[dict[str, Any]]:
        raw = self.database.get_stage_result(run_id, LandscapeWorkflowStep.PLAN_SEARCH.value)["value"]
        return list(raw.get("competitor_aliases", []))

    def report_alias_output(self, run_id: str) -> list[dict[str, Any]]:
        aliases = self.alias_output(run_id)
        plan = self.load_plan(run_id)
        searchable = " ".join(item.query_text for item in plan.queries).casefold()
        return [
            {
                **item,
                "searched_aliases": [
                    alias
                    for alias in item.get("aliases", [])
                    if alias.casefold() in searchable
                ],
                "unsearched_aliases": [
                    alias
                    for alias in item.get("aliases", [])
                    if alias.casefold() not in searchable
                ],
            }
            for item in aliases
        ]

    def direction_output(self, run_id: str) -> dict[str, Any] | None:
        raw = self.database.get_stage_result(
            run_id, LandscapeWorkflowStep.PLAN_SEARCH.value
        )["value"]
        return raw.get("technical_direction_expansion")

    def search_scope(self, run_id: str) -> LandscapeScope:
        """Return a provider-query scope expanded with non-authoritative aliases."""
        from .schemas import CompetitorAliasPlan

        scope = self.scope(run_id)
        aliases = self.alias_output(run_id)
        if not aliases:
            return scope
        return scope_with_alias_plan(scope, CompetitorAliasPlan(competitors=aliases))

    def selected_hits(self, run_id: str) -> list[MergedHit]:
        raw = self.database.get_stage_result(run_id, LandscapeWorkflowStep.FILTER_AND_SELECT.value)["value"]
        return [MergedHit.model_validate(item) for item in raw["result"]["candidates"]]

    async def _fetch_documents(
        self, run_id: str, hits: list[MergedHit]
    ) -> tuple[dict[str, FetchedDocument], dict[str, str]]:
        semaphore = asyncio.Semaphore(3)

        async def one(hit: MergedHit):
            async with semaphore:
                prefetched = self.prefetched_documents.get(run_id, {}).get(hit.publication_number or "")
                if prefetched is not None:
                    return hit.publication_number or hit.title, prefetched, None
                return await self._fetch_one(run_id, hit)

        results = await asyncio.gather(*(one(hit) for hit in hits))
        docs = {publication: document for publication, document, _ in results if document is not None}
        failures = {publication: error for publication, _, error in results if error is not None}
        return docs, failures

    async def _enrich_missing_dates(
        self, run_id: str, results: list[ProviderResult]
    ) -> list[ProviderResult]:
        """Use a bounded details call to recover authoritative publication dates.

        Search indexes often omit patent-specific dates. The strict filter still rejects a
        document when this enrichment cannot recover a real ISO publication date.
        """
        unique_requests: dict[tuple[str, str], SearchHit] = {}
        missing_hit_count = 0
        for result in results:
            if not result.succeeded:
                continue
            for hit in result.hits:
                if hit.publication_date or not hit.publication_number:
                    continue
                missing_hit_count += 1
                key = (hit.provider, hit.publication_number.replace(" ", "").upper())
                unique_requests.setdefault(key, hit)
        try:
            enrichment_limit = self.scope(run_id).budget.candidate_limit
        except KeyError:
            enrichment_limit = 100
        requests = list(unique_requests.items())[:enrichment_limit]
        self.enrichment_stats[run_id] = {
            "missing_hit_count": missing_hit_count,
            "unique_publication_count": len(unique_requests),
            "attempted_count": len(requests),
            "reused_hit_count": max(0, missing_hit_count - len(unique_requests)),
            "truncated_count": max(0, len(unique_requests) - len(requests)),
        }
        if not requests:
            return results
        semaphore = asyncio.Semaphore(2)
        prefetched: dict[str, FetchedDocument] = {}

        async def enrich(key: tuple[str, str], hit: SearchHit):
            provider = next((item for item in self.providers if item.name == hit.provider), None)
            if provider is None:
                return key, hit
            async with semaphore:
                fetched = await self.runner.fetch(
                    provider,
                    FetchRequest(
                        request_id=f"LM-{run_id[:8]}-{_identifier(hit.publication_number or '')}",
                        publication_number=hit.publication_number,
                        url=hit.url or None,
                    ),
                    timeout_seconds=self.provider_timeout_seconds.get(provider.name, 60),
                )
            if not fetched.succeeded or fetched.document is None:
                return key, hit
            document = fetched.document
            if not document.publication_date:
                return key, hit
            prefetched[hit.publication_number or ""] = document
            return (
                key,
                hit.model_copy(
                    update={
                        "publication_date": document.publication_date,
                        "filing_date": document.filing_date or hit.filing_date,
                        "assignee": document.assignee or hit.assignee,
                        "title": document.title or hit.title,
                        "family_id": document.family_id or hit.family_id,
                        "raw": {**hit.raw, "metadata_enriched": True},
                    }
                ),
            )

        enriched = await asyncio.gather(*(enrich(key, hit) for key, hit in requests))
        replacements = dict(enriched)
        updated: list[ProviderResult] = []
        for result in results:
            hits = [
                replacements.get(
                    (
                        hit.provider,
                        (hit.publication_number or "").replace(" ", "").upper(),
                    ),
                    hit,
                )
                for hit in result.hits
            ]
            updated.append(result.model_copy(update={"hits": hits}))
        self.prefetched_documents[run_id] = prefetched
        return updated

    async def _fetch_one(self, run_id: str, hit: MergedHit):
        publication = hit.publication_number or hit.title
        provider_order = list(hit.found_by)
        provider_order.extend(
            provider.name for provider in self.providers if provider.name not in provider_order
        )
        for provider_name in provider_order:
            provider = next((item for item in self.providers if item.name == provider_name), None)
            if provider is None:
                continue
            request = FetchRequest(
                request_id=f"LF-{run_id[:8]}-{_identifier(publication)}",
                publication_number=hit.publication_number,
                url=hit.urls[0] if hit.urls else None,
            )
            result = await self.runner.fetch(
                provider,
                request,
                timeout_seconds=self.provider_timeout_seconds.get(provider.name, 60),
            )
            if result.succeeded and result.document is not None:
                return publication, result.document, None
        return publication, None, "所有可用 Provider 均未能抓取详情。"

    @staticmethod
    def query_key(run_id: str, index: int) -> str:
        return f"{run_id}:LQ-{index}"


def coverage_limitations(coverage: dict[str, Any]) -> list[dict[str, str]]:
    limitations: list[dict[str, str]] = []
    raw_hit_count = int(coverage.get("raw_hit_count", 0))
    unique_candidate_count = int(coverage.get("unique_candidate_count", 0))
    statuses = list(coverage.get("provider_statuses", {}).values())
    if statuses and all(status == "EMPTY" for status in statuses):
        limitations.append(
            {
                "code": "SEARCH_EMPTY",
                "message": "已启用的检索 Provider 未返回任何原始专利命中。",
            }
        )
    elif raw_hit_count > 0 and unique_candidate_count == 0:
        limitations.append(
            {
                "code": "NO_ELIGIBLE_PATENTS",
                "message": "检索有返回，但没有专利同时满足公开日和友商范围。",
            }
        )
    if coverage.get("truncated_count"):
        limitations.append(
            {
                "code": "CANDIDATE_LIMIT",
                "message": f"候选集合超出预算，截断 {coverage['truncated_count']} 件。",
            }
        )
    for key, count in coverage.get("excluded_counts", {}).items():
        if count:
            limitations.append(
                {"code": key, "message": f"严格范围过滤排除 {count} 条命中。"}
            )
    failed_providers = [
        key
        for key, status in coverage.get("provider_statuses", {}).items()
        if status not in {"SUCCESS", "EMPTY"}
    ]
    if failed_providers:
        limitations.append(
            {
                "code": "PROVIDER_FAILURE",
                "message": "部分 Provider 不可用：" + ", ".join(failed_providers),
            }
        )
    return limitations


def document_id(publication: str) -> str:
    return "LD-" + hashlib.sha256(publication.upper().encode("utf-8")).hexdigest()[:24]


def _hit_id(run_id: str, query_id: str, hit: SearchHit) -> str:
    value = f"{run_id}|{query_id}|{hit.provider}|{hit.provider_rank}|{hit.publication_number}|{hit.url}"
    return "LH-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _identifier(value: str) -> str:
    return "".join(character for character in value.upper() if character.isalnum())


def _document_metadata(document: FetchedDocument) -> dict[str, Any]:
    return {
        "provider": document.provider,
        "publication_number": document.publication_number,
        "application_number": document.application_number,
        "family_id": document.family_id,
        "title": document.title,
        "assignee": document.assignee,
        "filing_date": document.filing_date,
        "publication_date": document.publication_date,
        "url": document.url,
        "language": document.language,
    }
