from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import date
from pathlib import Path

from idea.config import load_config
from idea.merge import merge_hits
from idea.model_client import StructuredModelClient
from idea.providers.base import FetchRequest, FetchedDocument, ProviderResult, SearchHit, SearchProvider
from landscape.database import LandscapeDatabase
from landscape.execution import LandscapeExecutionService, coverage_limitations
from landscape.reporting import LandscapeReportService
from landscape.schemas import (
    AnalysisBudget,
    AnalysisMode,
    CompetitorAliasPlan,
    CompetitorAliasResolution,
    CompetitorInput,
    LandscapeEvidenceRef,
    LandscapePatentAnalysis,
    LandscapeScope,
)
from landscape.search import LandscapeCandidateLimitExceededError
from landscape.store import LandscapeRunStore


class EnrichmentProvider(SearchProvider):
    name = "fixture_enrichment"

    def __init__(self):
        self.fetch_calls = 0

    async def search(self, query):
        return []

    async def fetch(self, request: FetchRequest) -> FetchedDocument:
        self.fetch_calls += 1
        return FetchedDocument(
            provider=self.name,
            publication_number=request.publication_number or "US1A1",
            application_number="US-APP-1",
            family_id="FAMILY-1",
            title="补全后的液冷专利",
            filing_date="2026-05-01",
            publication_date="2026-06-15",
            assignee="Example",
            url=request.url or "https://example.test/patent/US1A1",
            abstract_text="液冷摘要",
            claims_text="1. 一种液冷系统。",
            description_text="背景技术。",
            section_spans={
                "abstract": [{"label": "abstract", "start": 0, "end": 5, "text": "液冷摘要"}],
                "claims": [{"label": "claim 1", "start": 0, "end": 11, "text": "1. 一种液冷系统。"}],
                "description": [{"label": "background", "start": 0, "end": 5, "text": "背景技术。"}],
            },
        )


class CandidateCaptureRepository:
    def __init__(self):
        self.calls: list[tuple[str, list[dict]]] = []

    def put_candidates(self, run_id: str, candidates: list[dict]) -> list[dict]:
        self.calls.append((run_id, candidates))
        return candidates


class CompanyCaptureRepository:
    def __init__(self):
        self.calls = []

    def put_company_assignments(self, run_id, *, scope, result):
        self.calls.append((run_id, scope, result))
        return result


class FetchCaptureRepository:
    def __init__(self):
        self.documents = {}
        self.failures = {}

    def list_fetched_documents(self, _run_id):
        return dict(self.documents)

    def put_fetch_success(
        self, _run_id, *, document_id, publication_number, document
    ):
        self.documents[publication_number] = document
        self.failures.pop(publication_number, None)

    def put_fetch_failure(
        self, _run_id, *, document_id, publication_number, error_message
    ):
        self.failures[publication_number] = error_message


class AnalysisCaptureRepository:
    def __init__(self, analyses=None):
        self.analyses = dict(analyses or {})

    def list_patent_analyses(self, _run_id):
        return dict(self.analyses)


def _analysis(publication_number: str) -> LandscapePatentAnalysis:
    return LandscapePatentAnalysis(
        publication_number=publication_number,
        prior_art="现有方案",
        core_invention_points=["核心改进"],
        evidence_refs=[
            LandscapeEvidenceRef(
                evidence_id=f"EV-{publication_number}",
                supports=["prior_art", "core_invention_point"],
            )
        ],
    )


class LandscapeEnrichmentTests(unittest.TestCase):
    def test_model_alias_expands_search_but_cannot_authorize_filtering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = LandscapeDatabase(root / "landscape.db")
            database.initialize()
            store = LandscapeRunStore(root / "runs")
            company_repository = CompanyCaptureRepository()
            service = LandscapeExecutionService(
                database=database,
                store=store,
                model=StructuredModelClient(load_config().model),
                providers=[],
                provider_timeout_seconds={},
                analysis_concurrency=1,
                report_service=LandscapeReportService(database, store),
                company_repository=company_repository,
            )
            scope = LandscapeScope(
                mode=AnalysisMode.COMPETITOR,
                competitors=[
                    CompetitorInput(
                        name="Huawei",
                        aliases=["华为", "华为技术有限公司"],
                    )
                ],
                publication_start=date(2026, 4, 1),
                publication_end=date(2026, 6, 30),
            )
            run = database.create_run(
                scope=scope,
                model="fixture",
                workflow_version="1.0.0",
                prompt_version="1.0.0",
            )
            run_id = run["run_id"]

            async def resolve_aliases(_competitors):
                return CompetitorAliasPlan(
                    competitors=[
                        CompetitorAliasResolution(
                            primary_name="Huawei",
                            aliases=["Model Search Alias"],
                            source="MODEL_INFERRED",
                        )
                    ]
                )

            service.aliases.resolve = resolve_aliases
            plan_result = asyncio.run(service.plan_search(run_id))
            query_text = plan_result["plan"]["queries"][0]["query_text"]
            self.assertIn('"华为"', query_text)
            self.assertIn('"Model Search Alias"', query_text)
            database.put_stage_result(run_id, "PLAN_SEARCH", plan_result)

            self.assertEqual(
                service.search_scope(run_id).competitors[0].aliases,
                ["华为", "华为技术有限公司", "Model Search Alias"],
            )
            database.put_stage_result(
                run_id,
                "SEARCH_PUBLICATIONS",
                {
                    "results": [
                        ProviderResult(
                            provider="fixture",
                            operation="search",
                            request_id="LQ-1",
                            status="SUCCESS",
                            duration_ms=0,
                            hits=[
                                SearchHit(
                                    provider="fixture",
                                    provider_rank=1,
                                    title="inferred alias hit",
                                    url="https://example.test/US1A1",
                                    publication_number="US1A1",
                                    publication_date="2026-05-01",
                                    assignee="Model Search Alias Ltd",
                                ),
                                SearchHit(
                                    provider="fixture",
                                    provider_rank=2,
                                    title="confirmed alias hit",
                                    url="https://example.test/CN2A",
                                    publication_number="CN2A",
                                    publication_date="2026-05-02",
                                    assignee="华为技术有限公司",
                                ),
                            ],
                        ).model_dump(mode="json")
                    ]
                },
            )

            filtered = asyncio.run(service.filter_and_select(run_id))["result"]

            self.assertEqual(
                [item["publication_number"] for item in filtered["candidates"]],
                ["CN2A"],
            )
            self.assertEqual(
                filtered["coverage"]["excluded_counts"],
                {"COMPETITOR_NOT_CONFIRMED": 1},
            )
            self.assertEqual(
                filtered["coverage"]["company_patent_counts"][0]["company"],
                "Huawei",
            )
            _, assignment_scope, assignment_result = company_repository.calls[0]
            self.assertEqual(
                assignment_scope.competitors[0].aliases,
                ["华为", "华为技术有限公司"],
            )
            self.assertEqual(
                assignment_result.assignments[0].primary_company_id,
                "CO-HUAWEI",
            )

    def test_empty_search_and_filtered_empty_have_distinct_limitations(self) -> None:
        empty = coverage_limitations(
            {
                "raw_hit_count": 0,
                "unique_candidate_count": 0,
                "truncated_count": 0,
                "excluded_counts": {},
                "provider_statuses": {"LQ-1:serpapi_google_patents": "EMPTY"},
            }
        )
        self.assertEqual(empty[0]["code"], "SEARCH_EMPTY")
        filtered = coverage_limitations(
            {
                "raw_hit_count": 5,
                "unique_candidate_count": 0,
                "truncated_count": 0,
                "excluded_counts": {"COMPETITOR_NOT_CONFIRMED": 5},
                "provider_statuses": {"LQ-1:serpapi_google_patents": "SUCCESS"},
            }
        )
        self.assertEqual(filtered[0]["code"], "NO_ELIGIBLE_PATENTS")
        self.assertEqual(filtered[1]["code"], "COMPETITOR_NOT_CONFIRMED")

    def test_missing_search_date_is_recovered_only_from_detail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = LandscapeDatabase(root / "landscape.db")
            database.initialize()
            store = LandscapeRunStore(root / "runs")
            report = LandscapeReportService(database, store)
            provider = EnrichmentProvider()
            service = LandscapeExecutionService(
                database=database,
                store=store,
                model=StructuredModelClient(load_config().model),
                providers=[provider],
                provider_timeout_seconds={"fixture_enrichment": 1},
                analysis_concurrency=1,
                report_service=report,
            )
            result = ProviderResult(
                provider="fixture_enrichment",
                operation="search",
                request_id="LQ-1",
                status="SUCCESS",
                duration_ms=0,
                hits=[
                    SearchHit(
                        provider="fixture_enrichment",
                        provider_rank=1,
                        title="液冷",
                        url="https://example.test/patent/US1A1",
                        publication_number="US1A1",
                        publication_date=None,
                    )
                ],
            )
            duplicate = result.model_copy(update={"request_id": "LQ-2"})
            enriched = asyncio.run(
                service._enrich_missing_dates("run-1", [result, duplicate])
            )
            self.assertEqual(enriched[0].hits[0].publication_date, "2026-06-15")
            self.assertEqual(enriched[1].hits[0].publication_date, "2026-06-15")
            self.assertEqual(enriched[0].hits[0].application_number, "US-APP-1")
            self.assertEqual(enriched[0].hits[0].family_id, "FAMILY-1")
            self.assertIn("US1A1", service.prefetched_documents["run-1"])
            self.assertEqual(provider.fetch_calls, 1)
            self.assertEqual(service.enrichment_stats["run-1"]["reused_hit_count"], 1)

    def test_missing_family_identity_alone_does_not_fetch_full_text_during_filtering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = LandscapeDatabase(root / "landscape.db")
            database.initialize()
            provider = EnrichmentProvider()
            service = LandscapeExecutionService(
                database=database,
                store=LandscapeRunStore(root / "runs"),
                model=StructuredModelClient(load_config().model),
                providers=[provider],
                provider_timeout_seconds={"fixture_enrichment": 1},
                analysis_concurrency=1,
                report_service=LandscapeReportService(
                    database, LandscapeRunStore(root / "reports")
                ),
            )
            result = ProviderResult(
                provider="fixture_enrichment",
                operation="search",
                request_id="LQ-1",
                status="SUCCESS",
                duration_ms=0,
                hits=[
                    SearchHit(
                        provider="fixture_enrichment",
                        provider_rank=1,
                        title="液冷",
                        url="https://example.test/patent/US1A1",
                        publication_number="US1A1",
                        publication_date="2026-06-15",
                    )
                ],
            )

            enriched = asyncio.run(
                service._enrich_missing_dates("run-1", [result])
            )

            self.assertEqual(enriched, [result])
            self.assertEqual(provider.fetch_calls, 0)
            self.assertEqual(
                service.enrichment_stats["run-1"]["missing_family_identity_count"],
                1,
            )
            self.assertEqual(service.enrichment_stats["run-1"]["attempted_count"], 0)

    def test_filter_persists_complete_u_before_candidate_limit_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = LandscapeDatabase(root / "landscape.db")
            database.initialize()
            store = LandscapeRunStore(root / "runs")
            repository = CandidateCaptureRepository()
            company_repository = CompanyCaptureRepository()
            service = LandscapeExecutionService(
                database=database,
                store=store,
                model=StructuredModelClient(load_config().model),
                providers=[],
                provider_timeout_seconds={},
                analysis_concurrency=1,
                report_service=LandscapeReportService(database, store),
                candidate_repository=repository,
                company_repository=company_repository,
            )
            scope = LandscapeScope(
                mode=AnalysisMode.TECHNOLOGY,
                technology_direction="液冷",
                publication_start=date(2026, 4, 1),
                publication_end=date(2026, 6, 30),
                budget=AnalysisBudget(
                    candidate_limit=10,
                    analysis_limit=10,
                    per_query_limit=20,
                ),
            )
            run = database.create_run(
                scope=scope,
                model="fixture",
                workflow_version="1.0.0",
                prompt_version="1.0.0",
            )
            run_id = run["run_id"]
            database.put_queries(
                run_id,
                [
                    {
                        "query_id": service.query_key(run_id, 1),
                        "query_text": "液冷",
                        "language": "zh",
                        "rationale": "fixture",
                    }
                ],
            )
            database.put_stage_result(
                run_id,
                "PLAN_SEARCH",
                {
                    "plan": {
                        "direction_terms": ["液冷"],
                        "direction_english_terms": ["liquid cooling"],
                        "queries": [
                            {
                                "query_text": "液冷",
                                "language": "zh",
                                "rationale": "fixture",
                            }
                        ],
                    },
                    "competitor_aliases": [],
                },
            )
            database.put_stage_result(
                run_id,
                "SEARCH_PUBLICATIONS",
                {
                    "results": [
                        ProviderResult(
                            provider="fixture",
                            operation="search",
                            request_id="LQ-1",
                            status="SUCCESS",
                            duration_ms=0,
                            hits=[
                                SearchHit(
                                    provider="fixture",
                                    provider_rank=index,
                                    title=f"液冷专利 {index}",
                                    url=f"https://example.test/{index}",
                                    publication_number=f"US{index}A1",
                                    publication_date="2026-05-01",
                                )
                                for index in range(1, 12)
                            ],
                        ).model_dump(mode="json")
                    ]
                },
            )

            with self.assertRaises(LandscapeCandidateLimitExceededError) as raised:
                asyncio.run(service.filter_and_select(run_id))

            self.assertEqual(raised.exception.unique_candidate_count, 11)
            self.assertEqual(len(repository.calls), 1)
            persisted_run_id, candidates = repository.calls[0]
            self.assertEqual(persisted_run_id, run_id)
            self.assertEqual(len(candidates), 11)
            self.assertEqual(
                [candidate["rank"] for candidate in candidates],
                list(range(1, 12)),
            )
            self.assertEqual(
                len({candidate["publication_number"] for candidate in candidates}),
                11,
            )
            self.assertEqual(
                len(company_repository.calls[0][2].assignments),
                11,
            )

    def test_fetch_details_covers_all_u_and_resumes_completed_documents(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = LandscapeDatabase(root / "landscape.db")
            database.initialize()
            store = LandscapeRunStore(root / "runs")
            provider = EnrichmentProvider()
            fetch_repository = FetchCaptureRepository()
            service = LandscapeExecutionService(
                database=database,
                store=store,
                model=StructuredModelClient(load_config().model),
                providers=[provider],
                provider_timeout_seconds={"fixture_enrichment": 1},
                analysis_concurrency=1,
                report_service=LandscapeReportService(database, store),
                fetch_repository=fetch_repository,
            )
            scope = LandscapeScope(
                mode=AnalysisMode.TECHNOLOGY,
                technology_direction="液冷",
                publication_start=date(2026, 4, 1),
                publication_end=date(2026, 6, 30),
                budget=AnalysisBudget(
                    candidate_limit=10,
                    analysis_limit=3,
                    per_query_limit=10,
                ),
            )
            run = database.create_run(
                scope=scope,
                model="fixture",
                workflow_version="1.0.0",
                prompt_version="1.0.0",
            )
            database.put_stage_result(
                run["run_id"],
                "PLAN_SEARCH",
                {
                    "plan": {
                        "direction_terms": ["液冷"],
                        "direction_english_terms": ["liquid cooling"],
                        "queries": [
                            {
                                "query_text": "液冷专利",
                                "language": "zh",
                                "rationale": "中文检索",
                            },
                            {
                                "query_text": "liquid cooling patent",
                                "language": "en",
                                "rationale": "英文检索",
                            },
                        ],
                    },
                    "competitor_aliases": [],
                },
            )
            hits = [
                SearchHit(
                    provider="fixture_enrichment",
                    provider_rank=index,
                    title=f"液冷专利 {index}",
                    url=f"https://example.test/{index}",
                    publication_number=f"US{index}A1",
                    publication_date="2026-05-01",
                    assignee="Company A" if index in {1, 3, 5} else "Company B",
                )
                for index in range(1, 6)
            ]
            candidates = merge_hits([("LQ-1", hits)])
            database.put_stage_result(
                run["run_id"],
                "FILTER_AND_SELECT",
                {
                    "result": {
                        "candidates": [
                            candidate.model_dump(mode="json")
                            for candidate in candidates
                        ]
                    }
                },
            )

            async def fetch_batch(_run_id, batch):
                documents = {}
                failures = {}
                for candidate in batch:
                    publication = candidate.publication_number
                    if publication == "US1A1":
                        failures[publication] = "fixture failure"
                    else:
                        documents[publication] = FetchedDocument(
                            provider="fixture_enrichment",
                            publication_number=publication,
                            title=candidate.title,
                            url=candidate.urls[0],
                        )
                return documents, failures

            service._fetch_documents = fetch_batch
            result = asyncio.run(service.fetch_details(run["run_id"]))
            self.assertEqual(result["target_count"], 5)
            self.assertEqual(len(result["fetched_publications"]), 4)
            self.assertEqual(len(result["attempted_publications"]), 5)
            self.assertEqual(result["failures"], {"US1A1": "fixture failure"})
            self.assertFalse(result["complete"])

            attempted_on_resume = []

            async def fetch_resume(_run_id, batch):
                attempted_on_resume.extend(
                    candidate.publication_number for candidate in batch
                )
                return {
                    "US1A1": FetchedDocument(
                        provider="fixture_enrichment",
                        publication_number="US1A1",
                        title="液冷专利 1",
                        url="https://example.test/1",
                    )
                }, {}

            service._fetch_documents = fetch_resume
            resumed = asyncio.run(service.fetch_details(run["run_id"]))
            self.assertEqual(attempted_on_resume, ["US1A1"])
            self.assertEqual(resumed["resumed_fetched_count"], 4)
            self.assertEqual(len(resumed["fetched_publications"]), 5)
            self.assertTrue(resumed["complete"])

    def test_analysis_covers_all_f_and_resumes_persisted_results(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = LandscapeDatabase(root / "landscape.db")
            database.initialize()
            store = LandscapeRunStore(root / "runs")
            existing = {"US1A1": _analysis("US1A1")}
            analysis_repository = AnalysisCaptureRepository(existing)
            service = LandscapeExecutionService(
                database=database,
                store=store,
                model=StructuredModelClient(load_config().model),
                providers=[],
                provider_timeout_seconds={},
                analysis_concurrency=1,
                report_service=LandscapeReportService(database, store),
                analysis_repository=analysis_repository,
            )
            scope = LandscapeScope(
                technology_direction="液冷",
                publication_start=date(2026, 4, 1),
                publication_end=date(2026, 6, 30),
                budget=AnalysisBudget(
                    candidate_limit=10,
                    analysis_limit=1,
                    per_query_limit=10,
                ),
            )
            run = database.create_run(
                scope=scope,
                model="fixture",
                workflow_version="1.0.0",
                prompt_version="1.0.0",
            )
            run_id = run["run_id"]
            database.put_stage_result(
                run_id,
                "PLAN_SEARCH",
                {
                    "plan": {
                        "direction_terms": ["液冷"],
                        "direction_english_terms": ["liquid cooling"],
                        "queries": [
                            {
                                "query_text": "液冷",
                                "language": "zh",
                                "rationale": "fixture",
                            }
                        ],
                    },
                    "competitor_aliases": [],
                },
            )
            service.documents[run_id] = {
                f"US{index}A1": FetchedDocument(
                    provider="fixture",
                    publication_number=f"US{index}A1",
                    title=f"Patent {index}",
                    url=f"https://example.test/{index}",
                )
                for index in range(1, 4)
            }
            analyzed_batches = []

            async def analyze_many(*, run_id, documents, direction_terms):
                analyzed_batches.append(
                    [document.publication_number for _, document in documents]
                )
                return {
                    document.publication_number: _analysis(
                        document.publication_number
                    )
                    for _, document in documents
                }, {}

            service.analysis.analyze_many = analyze_many
            result = asyncio.run(service.analyze_patents(run_id))

            self.assertEqual(analyzed_batches, [["US2A1", "US3A1"]])
            self.assertEqual(result["target_count"], 3)
            self.assertEqual(result["resumed_analysis_count"], 1)
            self.assertEqual(result["analyzed_count"], 3)
            self.assertTrue(result["complete"])


if __name__ == "__main__":
    unittest.main()
