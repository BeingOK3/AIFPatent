from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Iterable
from typing import Any

from idea.merge import MergedHit
from idea.model_client import StructuredModelClient
from idea.providers.base import FetchRequest, FetchedDocument, ProviderResult, ProviderRunner, SearchHit, SearchProvider

from .analysis import LandscapeAnalysisService
from .clustering import LandscapeClusteringError, LandscapeClusteringService
from .database import LandscapeDatabase
from .planning import build_deterministic_query_plan
from .reporting import LandscapeReportService, build_report
from .schemas import LandscapeQueryPlan, LandscapeScope
from .search import (
    execute_provider_queries,
    exclusion_reason,
    strict_filter_and_select,
)
from .store import LandscapeRunStore
from .workflow import LandscapeWorkflowStep


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
        self.report_service = report_service
        self.documents: dict[str, dict[str, FetchedDocument]] = {}
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
        plan = build_deterministic_query_plan(scope)
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
        return {"plan": plan.model_dump(mode="json")}

    async def search_publications(self, run_id: str) -> dict[str, Any]:
        scope = self.scope(run_id)
        plan = self.load_plan(run_id)
        results = await execute_provider_queries(
            scope=scope,
            plan=plan,
            providers=self.providers,
            runner=self.runner,
            timeout_seconds=max(self.provider_timeout_seconds.values(), default=30),
        )
        return {"results": [result.model_dump(mode="json") for result in results]}

    async def filter_and_select(self, run_id: str) -> dict[str, Any]:
        scope = self.scope(run_id)
        raw = self.database.get_stage_result(run_id, LandscapeWorkflowStep.SEARCH_PUBLICATIONS.value)["value"]
        results = [ProviderResult.model_validate(item) for item in raw["results"]]
        batches = [(result.request_id, result.hits) for result in results if result.succeeded]
        statuses = {f"{result.request_id}:{result.provider}": result.status.value for result in results}
        result = strict_filter_and_select(batches, scope=scope, provider_statuses=statuses)
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
                    normalized_key=hit.publication_number,
                    decision=decision,
                    exclusion_reason=reason.value if reason else None,
                    raw=hit.model_dump(mode="json"),
                )
        return {"result": result.model_dump(mode="json")}

    async def fetch_details(self, run_id: str) -> dict[str, Any]:
        scope = self.scope(run_id)
        selected = self.selected_hits(run_id)
        selected = selected[: scope.budget.analysis_limit]
        docs, failures = await self._fetch_documents(run_id, selected)
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
            "selected_publications": [hit.publication_number for hit in selected if hit.publication_number],
            "fetched_publications": sorted(docs),
            "failures": failures,
        }

    async def analyze_patents(self, run_id: str) -> dict[str, Any]:
        scope = self.scope(run_id)
        docs = self.documents.get(run_id)
        if docs is None:
            selected = self.selected_hits(run_id)[: scope.budget.analysis_limit]
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
        )
        self.report_service.save(run_id, report)
        return {"report": report, "manifest": "manifest.json"}

    def collect_limitations(self, run_id: str) -> list[dict[str, Any]]:
        limitations: list[dict[str, Any]] = []
        try:
            filtered = self.database.get_stage_result(run_id, LandscapeWorkflowStep.FILTER_AND_SELECT.value)["value"]["result"]
            coverage = filtered["coverage"]
            if coverage["truncated_count"]:
                limitations.append({"code": "CANDIDATE_LIMIT", "message": f"候选集合超出预算，截断 {coverage['truncated_count']} 件。"})
            for key, count in coverage.get("excluded_counts", {}).items():
                if count:
                    limitations.append({"code": key, "message": f"严格范围过滤排除 {count} 条命中。"})
            failed_providers = [key for key, status in coverage.get("provider_statuses", {}).items() if status not in {"SUCCESS", "EMPTY"}]
            if failed_providers:
                limitations.append({"code": "PROVIDER_FAILURE", "message": "部分 Provider 不可用：" + ", ".join(failed_providers)})
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
        return limitations

    def scope(self, run_id: str) -> LandscapeScope:
        return LandscapeScope.model_validate(self.database.get_run(run_id)["scope_json"])

    def load_plan(self, run_id: str) -> LandscapeQueryPlan:
        raw = self.database.get_stage_result(run_id, LandscapeWorkflowStep.PLAN_SEARCH.value)["value"]
        return LandscapeQueryPlan.model_validate(raw["plan"])

    def selected_hits(self, run_id: str) -> list[MergedHit]:
        raw = self.database.get_stage_result(run_id, LandscapeWorkflowStep.FILTER_AND_SELECT.value)["value"]
        return [MergedHit.model_validate(item) for item in raw["result"]["candidates"]]

    async def _fetch_documents(
        self, run_id: str, hits: list[MergedHit]
    ) -> tuple[dict[str, FetchedDocument], dict[str, str]]:
        semaphore = asyncio.Semaphore(3)

        async def one(hit: MergedHit):
            async with semaphore:
                return await self._fetch_one(run_id, hit)

        results = await asyncio.gather(*(one(hit) for hit in hits))
        docs = {publication: document for publication, document, _ in results if document is not None}
        failures = {publication: error for publication, _, error in results if error is not None}
        return docs, failures

    async def _fetch_one(self, run_id: str, hit: MergedHit):
        publication = hit.publication_number or hit.title
        for provider_name in hit.found_by or [provider.name for provider in self.providers]:
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
