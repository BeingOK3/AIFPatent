from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Iterable
from datetime import date
from typing import Any, Protocol

from idea.merge import MergedHit
from idea.merge import normalize_publication_number
from idea.model_client import StructuredModelClient
from idea.providers.base import FetchRequest, FetchedDocument, ProviderResult, ProviderRunner, SearchHit, SearchProvider

from .analysis import LandscapeAnalysisService
from .company_assignment import CompanyAssignmentResult, assign_companies
from .company_batches import build_company_analysis_batches
from .company_classification import (
    CompanyTechnologyClassificationService,
    validate_company_technology_classification,
)
from .company_profiles import CompanyTechnologyProfileService
from .company_trends import (
    CrossCompanyTrendService,
    validate_cross_company_trend_proposal,
)
from .coverage_audit import audit_company_trend_coverage
from .database import LandscapeDatabase
from .planning import (
    CompetitorAliasService,
    TechnicalDirectionService,
    build_deterministic_query_plan,
    fallback_alias_plan,
    scope_with_alias_plan,
)
from .reporting import LandscapeReportService, build_report
from .schemas import (
    CompanyTechnologyClassification,
    CompanyTechnologyProfile,
    CrossCompanyTrendAnalysis,
    CrossCompanyTrendProposal,
    CrossCompanyTrendProposalAnalysis,
    LandscapeCoverageAudit,
    LandscapePatentAnalysis,
    LandscapeQueryPlan,
    LandscapeScope,
    TrendTimeBasis,
)
from .search import (
    LandscapeCandidateLimitExceededError,
    execute_provider_queries,
    exclusion_reason,
    family_publication_numbers,
    strict_filter_and_select,
)
from .store import LandscapeRunStore
from .workflow import (
    LandscapeWorkflowStep,
    NonRetryableLandscapeWorkflowError,
)


class LandscapeCandidateRepository(Protocol):
    def put_candidates(
        self, run_id: str, candidates: list[dict[str, Any]]
    ) -> list[dict[str, Any]]: ...

    def list_candidates(self, run_id: str) -> list[dict[str, Any]]: ...


class LandscapeCompanyRepository(Protocol):
    def put_company_assignments(
        self,
        run_id: str,
        *,
        scope: LandscapeScope,
        result: CompanyAssignmentResult,
    ) -> CompanyAssignmentResult: ...

    def list_company_assignments(self, run_id: str) -> CompanyAssignmentResult: ...


class LandscapeFetchRepository(Protocol):
    def list_fetched_documents(
        self, run_id: str
    ) -> dict[str, FetchedDocument]: ...

    def put_fetch_success(
        self,
        run_id: str,
        *,
        document_id: str,
        publication_number: str,
        document: FetchedDocument,
    ) -> None: ...

    def put_fetch_failure(
        self,
        run_id: str,
        *,
        document_id: str,
        publication_number: str,
        error_message: str,
    ) -> None: ...


class LandscapeAnalysisRepository(Protocol):
    def list_patent_analyses(
        self, run_id: str
    ) -> dict[str, LandscapePatentAnalysis]: ...


class LandscapeCompanyProfileRepository(Protocol):
    def list_company_profiles(
        self, run_id: str
    ) -> dict[str, CompanyTechnologyProfile]: ...

    def put_company_profile(
        self,
        run_id: str,
        *,
        company_id: str,
        profile: CompanyTechnologyProfile,
    ) -> CompanyTechnologyProfile: ...

    def put_repaired_company_profile(
        self,
        run_id: str,
        *,
        repair_round: int,
        company_id: str,
        profile: CompanyTechnologyProfile,
    ) -> CompanyTechnologyProfile: ...


class LandscapeCompanyFanoutRunner(Protocol):
    async def execute(
        self, run_id: str, company_ids: list[str]
    ) -> list[str]: ...


class LandscapeTrendRepository(Protocol):
    def list_cross_company_analysis(
        self, run_id: str
    ) -> CrossCompanyTrendAnalysis | None: ...

    def put_cross_company_analysis(
        self, run_id: str, analysis: CrossCompanyTrendAnalysis
    ) -> CrossCompanyTrendAnalysis: ...

    def put_repaired_cross_company_analysis(
        self,
        run_id: str,
        *,
        repair_round: int,
        analysis: CrossCompanyTrendAnalysis,
    ) -> CrossCompanyTrendAnalysis: ...


class LandscapeCoverageAuditRepository(Protocol):
    def list_coverage_audits(
        self, run_id: str
    ) -> list[LandscapeCoverageAudit]: ...

    def put_coverage_audit(
        self,
        run_id: str,
        *,
        repair_round: int,
        audit: LandscapeCoverageAudit,
    ) -> LandscapeCoverageAudit: ...


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
        fetch_repository: "LandscapeFetchRepository | None" = None,
        analysis_repository: "LandscapeAnalysisRepository | None" = None,
        profile_repository: "LandscapeCompanyProfileRepository | None" = None,
        company_fanout: "LandscapeCompanyFanoutRunner | None" = None,
        trend_repository: "LandscapeTrendRepository | None" = None,
        audit_repository: "LandscapeCoverageAuditRepository | None" = None,
    ):
        self.database = database
        self.store = store
        self.providers = providers
        self.provider_timeout_seconds = provider_timeout_seconds
        self.runner = ProviderRunner()
        self.analysis = LandscapeAnalysisService(
            model, database, concurrency=analysis_concurrency
        )
        self.aliases = CompetitorAliasService(model)
        self.directions = TechnicalDirectionService(model)
        self.company_classifier = CompanyTechnologyClassificationService(model)
        self.company_profiler = CompanyTechnologyProfileService(model)
        self.company_trends = CrossCompanyTrendService(model)
        self.report_service = report_service
        self.candidate_repository = candidate_repository
        self.company_repository = company_repository
        self.fetch_repository = fetch_repository
        self.analysis_repository = analysis_repository
        self.profile_repository = profile_repository
        self.company_fanout = company_fanout
        self.trend_repository = trend_repository
        self.audit_repository = audit_repository
        self.documents: dict[str, dict[str, FetchedDocument]] = {}
        self.prefetched_documents: dict[str, dict[str, FetchedDocument]] = {}
        self.enrichment_stats: dict[str, dict[str, int]] = {}

    def bind_company_fanout(
        self, company_fanout: LandscapeCompanyFanoutRunner
    ) -> None:
        if self.company_fanout is not None:
            raise RuntimeError("company fan-out is already bound")
        self.company_fanout = company_fanout

    async def analyze_company(
        self,
        run_id: str,
        company_id: str,
        *,
        repair_round: int | None = None,
    ) -> dict[str, Any]:
        if (
            self.company_repository is None
            or self.analysis_repository is None
            or self.profile_repository is None
        ):
            raise RuntimeError(
                "company analysis requires assignment, analysis and profile repositories"
            )
        assignment_result = self.company_repository.list_company_assignments(
            run_id
        )
        analyses = self.analysis_repository.list_patent_analyses(run_id)
        batches = build_company_analysis_batches(assignment_result, analyses)
        batch = next(
            (candidate for candidate in batches if candidate.company_id == company_id),
            None,
        )
        if batch is None:
            raise ValueError(f"unknown or empty company analysis batch: {company_id}")

        existing = self.profile_repository.list_company_profiles(run_id)
        profile = existing.get(company_id)
        force_rebuild = repair_round is not None
        if force_rebuild and repair_round < 1:
            raise ValueError("company repair requires repair_round >= 1")
        recovered = profile is not None
        if profile is not None and not force_rebuild:
            validate_company_technology_classification(
                batch,
                CompanyTechnologyClassification(
                    technology_categories=profile.technology_categories
                ),
            )
        else:
            classification = await self.company_classifier.classify(batch)
            profile = await self.company_profiler.build(batch, classification)
            if force_rebuild:
                self.profile_repository.put_repaired_company_profile(
                    run_id,
                    repair_round=repair_round,
                    company_id=company_id,
                    profile=profile,
                )
            else:
                self.profile_repository.put_company_profile(
                    run_id,
                    company_id=company_id,
                    profile=profile,
                )
        return {
            "company_id": company_id,
            "publication_count": len(batch.items),
            "category_count": len(profile.technology_categories),
            "recovered": recovered and not force_rebuild,
            "repair_round": repair_round,
        }

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
            LandscapeWorkflowStep.ANALYZE_COMPANIES: self.analyze_companies,
            LandscapeWorkflowStep.ANALYZE_CROSS_COMPANY_TRENDS: (
                self.analyze_cross_company_trends
            ),
            LandscapeWorkflowStep.VERIFY_COVERAGE: self.verify_coverage,
            LandscapeWorkflowStep.BUILD_REPORT: self.build_report,
        }
        return await handlers[step](run_id)

    async def analyze_companies(self, run_id: str) -> dict[str, Any]:
        if (
            self.company_repository is None
            or self.analysis_repository is None
            or self.company_fanout is None
        ):
            raise RuntimeError(
                "company fan-out requires assignment, analysis and fan-out services"
            )
        assignments = self.company_repository.list_company_assignments(run_id)
        analyses = self.analysis_repository.list_patent_analyses(run_id)
        batches = build_company_analysis_batches(assignments, analyses)
        company_ids = [batch.company_id for batch in batches]
        completed = await self.company_fanout.execute(run_id, company_ids)
        return {
            "company_count": len(company_ids),
            "company_ids": company_ids,
            "completed_company_ids": completed,
        }

    async def analyze_cross_company_trends(
        self, run_id: str, *, repair_round: int | None = None
    ) -> dict[str, Any]:
        if (
            self.profile_repository is None
            or self.analysis_repository is None
            or self.fetch_repository is None
            or self.trend_repository is None
        ):
            raise RuntimeError(
                "trend analysis requires profile, analysis, fetch and trend repositories"
            )
        scope = self.scope(run_id)
        profiles = self.profile_repository.list_company_profiles(run_id)
        analyses = self.analysis_repository.list_patent_analyses(run_id)
        documents = self.fetch_repository.list_fetched_documents(run_id)
        publication_dates: dict[str, date] = {}
        for publication in analyses:
            document = documents.get(publication)
            if document is None or not document.publication_date:
                raise ValueError(
                    f"trend input lacks publication date: {publication}"
                )
            publication_dates[publication] = date.fromisoformat(
                document.publication_date[:10]
            )
        time_basis = TrendTimeBasis(
            start=scope.publication_start,
            end=scope.publication_end,
            bucket="QUARTER",
        )
        existing = self.trend_repository.list_cross_company_analysis(run_id)
        force_rebuild = repair_round is not None
        if force_rebuild and repair_round < 1:
            raise ValueError("trend repair requires repair_round >= 1")
        recovered = existing is not None and not force_rebuild
        if existing is not None and not force_rebuild:
            _validate_recovered_trends(
                existing,
                profiles=profiles,
                analyses=analyses,
                publication_dates=publication_dates,
                time_basis=time_basis,
            )
            analysis = existing
        else:
            analysis = await self.company_trends.analyze(
                profiles=profiles,
                analyses=analyses,
                publication_dates=publication_dates,
                time_basis=time_basis,
            )
            if force_rebuild:
                self.trend_repository.put_repaired_cross_company_analysis(
                    run_id, repair_round=repair_round, analysis=analysis
                )
            else:
                self.trend_repository.put_cross_company_analysis(run_id, analysis)
        return {
            "trend_count": len(analysis.trends),
            "company_count": len(profiles),
            "recovered": recovered,
            "repair_round": repair_round,
        }

    async def verify_coverage(self, run_id: str) -> dict[str, Any]:
        if (
            self.candidate_repository is None
            or self.company_repository is None
            or self.fetch_repository is None
            or self.analysis_repository is None
            or self.profile_repository is None
            or self.trend_repository is None
            or self.audit_repository is None
        ):
            raise RuntimeError("coverage audit requires all landscape repositories")
        existing = self.audit_repository.list_coverage_audits(run_id)
        if existing:
            audit = existing[-1]
            recovered = True
            repair_round = len(existing) - 1
        else:
            candidates = self.candidate_repository.list_candidates(run_id)
            assignments = self.company_repository.list_company_assignments(run_id)
            documents = self.fetch_repository.list_fetched_documents(run_id)
            analyses = self.analysis_repository.list_patent_analyses(run_id)
            profiles = self.profile_repository.list_company_profiles(run_id)
            trends = self.trend_repository.list_cross_company_analysis(run_id)
            evidence = {
                publication: {
                    reference.evidence_id
                    for reference in analysis.evidence_refs
                }
                for publication, analysis in analyses.items()
            }
            repair_round = 0
            audit = audit_company_trend_coverage(
                eligible_publications=[
                    candidate["publication_number"]
                    for candidate in candidates
                ],
                assignment_result=assignments,
                fetched_publications=set(documents),
                analyses=analyses,
                profiles=profiles,
                trends=trends,
                valid_evidence_ids_by_publication=evidence,
                repair_round=repair_round,
                max_repair_rounds=0,
            )
            self.audit_repository.put_coverage_audit(
                run_id,
                repair_round=repair_round,
                audit=audit,
            )
            recovered = False
        if audit.decision == "FAIL":
            raise NonRetryableLandscapeWorkflowError(
                "coverage audit rejected corrupt landscape result"
            )
        return {
            "decision": audit.decision,
            "coverage_ratio": audit.coverage_ratio,
            "repair_round": repair_round,
            "missing_publications": audit.missing_publications,
            "repair_targets": audit.repair_targets,
            "limitations": audit.limitations,
            "recovered": recovered,
        }

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
                            "family_publication_numbers": family_publication_numbers(
                                candidate
                            ),
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
        candidates = self.selected_hits(run_id)
        docs = (
            self.fetch_repository.list_fetched_documents(run_id)
            if self.fetch_repository is not None
            else {}
        )
        pending = [
            hit
            for hit in candidates
            if hit.publication_number not in docs
        ]
        fetched, failures = await self._fetch_documents(run_id, pending)
        docs.update(fetched)
        self.documents[run_id] = docs
        for publication, document in fetched.items():
            if self.fetch_repository is not None:
                self.fetch_repository.put_fetch_success(
                    run_id,
                    document_id=document_id(publication),
                    publication_number=publication,
                    document=document,
                )
        for publication, error in failures.items():
            if self.fetch_repository is not None:
                self.fetch_repository.put_fetch_failure(
                    run_id,
                    document_id=document_id(publication),
                    publication_number=publication,
                    error_message=error,
                )
        for publication, document in docs.items():
            self.database.put_document(
                run_id,
                document_id=document_id(publication),
                publication_number=publication,
                status="FETCHED",
                metadata=_document_metadata(document),
            )
        return {
            "target_count": len(candidates),
            "selected_publications": [
                hit.publication_number for hit in candidates if hit.publication_number
            ],
            "attempted_publications": [
                hit.publication_number for hit in pending if hit.publication_number
            ],
            "fetched_publications": sorted(docs),
            "resumed_fetched_count": len(docs) - len(fetched),
            "complete": len(docs) == len(candidates),
            "failures": failures,
        }

    async def analyze_patents(self, run_id: str) -> dict[str, Any]:
        scope = self.scope(run_id)
        docs = self.documents.get(run_id)
        if docs is None:
            if self.fetch_repository is not None:
                docs = self.fetch_repository.list_fetched_documents(run_id)
            else:
                fetch_result = self.database.get_stage_result(
                    run_id, LandscapeWorkflowStep.FETCH_DETAILS.value
                )["value"]
                fetched_publications = set(
                    fetch_result.get("fetched_publications", [])
                )
                selected = [
                    hit
                    for hit in self.selected_hits(run_id)
                    if hit.publication_number in fetched_publications
                ]
                docs, _ = await self._fetch_documents(run_id, selected)
            self.documents[run_id] = docs
        analyses = (
            self.analysis_repository.list_patent_analyses(run_id)
            if self.analysis_repository is not None
            else {}
        )
        outside_fetched = sorted(set(analyses) - set(docs))
        if outside_fetched:
            raise ValueError(
                "persisted analyses reference documents outside fetched set: "
                + ", ".join(outside_fetched)
            )
        plan = self.load_plan(run_id)
        direction_terms = plan.direction_terms or ([scope.technology_direction] if scope.technology_direction else [])
        pending = [
            (document_id(publication), document)
            for publication, document in sorted(docs.items())
            if publication not in analyses
        ]
        new_analyses, failures = await self.analysis.analyze_many(
            run_id=run_id,
            documents=pending,
            direction_terms=direction_terms,
        )
        analyses.update(new_analyses)
        for publication in new_analyses:
            self.database.put_document(
                run_id,
                document_id=document_id(publication),
                publication_number=publication,
                status="ANALYZED",
                metadata=_document_metadata(docs[publication]),
            )
        return {
            "target_count": len(docs),
            "resumed_analysis_count": len(analyses) - len(new_analyses),
            "analyzed_count": len(analyses),
            "complete": len(analyses) == len(docs),
            "analyses": {
                key: value.model_dump(mode="json")
                for key, value in sorted(analyses.items())
            },
            "failures": failures,
        }

    async def build_report(self, run_id: str) -> dict[str, Any]:
        from .schemas import LandscapePatentAnalysis

        run = self.database.get_run(run_id)
        coverage = self.database.get_stage_result(run_id, LandscapeWorkflowStep.FILTER_AND_SELECT.value)["value"]["result"]["coverage"]
        analysis_raw = self.database.get_stage_result(run_id, LandscapeWorkflowStep.ANALYZE_PATENTS.value)["value"]
        analyses = {key: LandscapePatentAnalysis.model_validate(value) for key, value in analysis_raw["analyses"].items()}
        profiles = (
            self.profile_repository.list_company_profiles(run_id)
            if self.profile_repository is not None
            else {}
        )
        cross_company_analysis = (
            self.trend_repository.list_cross_company_analysis(run_id)
            if self.trend_repository is not None
            else None
        )
        try:
            company_trend_coverage = self.database.get_stage_result(
                run_id, LandscapeWorkflowStep.VERIFY_COVERAGE.value
            )["value"]
        except KeyError:
            company_trend_coverage = None
        limitations = self.collect_limitations(run_id)
        report = build_report(
            run=run,
            coverage=coverage,
            documents=self.documents.get(run_id, {}),
            analyses=analyses,
            failures=analysis_raw["failures"],
            limitations=limitations,
            searched_competitor_aliases=self.report_alias_output(run_id),
            technical_direction_expansion=self.direction_output(run_id),
            company_profiles=profiles,
            cross_company_analysis=cross_company_analysis,
            company_trend_coverage=company_trend_coverage,
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
        try:
            audit = self.database.get_stage_result(
                run_id, LandscapeWorkflowStep.VERIFY_COVERAGE.value
            )["value"]
            if audit.get("decision") == "LIMITED":
                messages = audit.get("limitations") or [
                    "公司趋势覆盖未达到完整报告门槛。"
                ]
                limitations.extend(
                    {
                        "code": "COVERAGE_LIMITED",
                        "message": message,
                    }
                    for message in messages
                )
        except KeyError:
            pass
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
        """Use bounded detail calls only when an authoritative date is missing.

        A date-recovery response also contributes its application/family identity
        and is cached for FETCH_DETAILS. Missing family identity alone deliberately
        does not trigger a details call: doing that here would turn deduplication
        into an unbounded full-text crawl.
        """
        unique_requests: dict[tuple[str, str], SearchHit] = {}
        missing_hit_count = 0
        missing_family_identity_count = 0
        for result in results:
            if not result.succeeded:
                continue
            for hit in result.hits:
                if not hit.publication_number:
                    continue
                missing_date = not hit.publication_date
                missing_family_identity = not hit.family_id
                if missing_family_identity:
                    missing_family_identity_count += 1
                if not missing_date:
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
            "missing_family_identity_count": missing_family_identity_count,
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
            document = fetched.document.model_copy(
                update={
                    "publication_date": (
                        fetched.document.publication_date or hit.publication_date
                    )
                }
            )
            if not document.publication_date:
                return key, hit
            prefetched[hit.publication_number or ""] = document
            return (
                key,
                hit.model_copy(
                    update={
                        "publication_date": document.publication_date,
                        "application_number": (
                            document.application_number or hit.application_number
                        ),
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


def _validate_recovered_trends(
    analysis: CrossCompanyTrendAnalysis,
    *,
    profiles: dict[str, CompanyTechnologyProfile],
    analyses: dict[str, LandscapePatentAnalysis],
    publication_dates: dict[str, date],
    time_basis: TrendTimeBasis,
) -> None:
    company_by_publication = {
        publication: company_id
        for company_id, profile in profiles.items()
        for category in profile.technology_categories
        for publication in category.publication_numbers
    }
    if set(company_by_publication) != set(analyses):
        raise ValueError(
            "recovered trend profiles do not cover current analyses"
        )
    if set(publication_dates) != set(analyses):
        raise ValueError(
            "recovered trend dates do not cover current analyses"
        )
    evidence_by_publication = {
        publication: {
            reference.evidence_id for reference in patent.evidence_refs
        }
        for publication, patent in analyses.items()
    }
    bucket_by_publication = {
        publication: (
            f"{published.year:04d}-Q{(published.month - 1) // 3 + 1}"
        )
        for publication, published in publication_dates.items()
    }
    if any(trend.time_basis != time_basis for trend in analysis.trends):
        raise ValueError("recovered trend time basis does not match run scope")
    validate_cross_company_trend_proposal(
        CrossCompanyTrendProposalAnalysis(
            overall_summary=analysis.overall_summary,
            common_directions=analysis.common_directions,
            differentiated_directions=analysis.differentiated_directions,
            trends=[
                CrossCompanyTrendProposal(
                    name=trend.name,
                    summary=trend.summary,
                    direction=trend.direction,
                    company_ids=trend.company_ids,
                    publication_numbers=trend.publication_numbers,
                    evidence_ids=trend.evidence_ids,
                )
                for trend in analysis.trends
            ],
            limitations=analysis.limitations,
        ),
        company_by_publication=company_by_publication,
        evidence_by_publication=evidence_by_publication,
        bucket_by_publication=bucket_by_publication,
        minimum_patents_for_time_trend=3,
    )


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
