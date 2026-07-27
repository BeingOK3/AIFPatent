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
from landscape.schemas import AnalysisBudget, AnalysisMode, LandscapeScope
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


class LandscapeEnrichmentTests(unittest.TestCase):
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
            self.assertIn("US1A1", service.prefetched_documents["run-1"])
            self.assertEqual(provider.fetch_calls, 1)
            self.assertEqual(service.enrichment_stats["run-1"]["reused_hit_count"], 1)

    def test_fetch_details_backfills_failed_primary_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = LandscapeDatabase(root / "landscape.db")
            database.initialize()
            store = LandscapeRunStore(root / "runs")
            provider = EnrichmentProvider()
            service = LandscapeExecutionService(
                database=database,
                store=store,
                model=StructuredModelClient(load_config().model),
                providers=[provider],
                provider_timeout_seconds={"fixture_enrichment": 1},
                analysis_concurrency=1,
                report_service=LandscapeReportService(database, store),
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
            self.assertEqual(result["target_count"], 3)
            self.assertEqual(len(result["fetched_publications"]), 3)
            self.assertEqual(len(result["attempted_publications"]), 4)
            self.assertEqual(result["attempted_publications"][-1], "US5A1")
            self.assertEqual(result["backfilled_count"], 1)
            self.assertEqual(result["failures"], {"US1A1": "fixture failure"})


if __name__ == "__main__":
    unittest.main()
