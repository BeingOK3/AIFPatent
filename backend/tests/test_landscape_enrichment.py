from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import date
from pathlib import Path

from idea.config import load_config
from idea.model_client import StructuredModelClient
from idea.providers.base import FetchRequest, FetchedDocument, ProviderResult, SearchHit, SearchProvider
from landscape.database import LandscapeDatabase
from landscape.execution import LandscapeExecutionService
from landscape.reporting import LandscapeReportService
from landscape.schemas import AnalysisMode, LandscapeScope
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


if __name__ == "__main__":
    unittest.main()
