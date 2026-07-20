from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from idea.cache import CacheStore
from idea.config import load_config
from idea.database import Database
from idea.providers import GooglePatentsProvider, ProviderRunner, ProviderStatus, SearchQuery


JSON_FIXTURE = json.dumps(
    {
        "results": {
            "total_num_results": 2,
            "cluster": [
                {
                    "result": [
                        {
                            "id": "patent/US10893120B2/en",
                            "patent": {
                                "title": "Data <b>caching</b> and data-aware placement",
                                "snippet": "Schedule a job according to cache &amp; locality.",
                                "priority_date": "2017-08-15",
                                "filing_date": "2018-08-15",
                                "publication_date": "2021-01-12",
                                "assignee": "International Business Machines Corp",
                                "publication_number": "US10893120B2",
                                "language": "en",
                            },
                        },
                        {
                            "id": "patent/EP0965918A2/en",
                            "patent": {
                                "title": "Cache affinity based scheduling",
                                "snippet": "Measure a cache footprint.",
                                "priority_date": "1998-06-17",
                                "assignee": "International Business Machines Corp",
                                "publication_number": "EP0965918A2",
                                "language": "en",
                            },
                        },
                    ]
                }
            ],
        }
    }
).encode()


class GooglePatentsSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.config = load_config()
        self.settings = self.config.search.providers.google_patents_local.model_copy(
            update={"min_request_interval_seconds": 0, "max_attempts": 1}
        )
        self.calls: list[str] = []

        async def getter(url: str) -> bytes:
            self.calls.append(url)
            return JSON_FIXTURE

        self.getter = getter

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def query(round_number=1, limit=10):
        return SearchQuery(
            query_id="Q-GPAT-1",
            text="cache affinity scheduling",
            round_number=round_number,
            limit=limit,
            query_type="technical_means",
            countries=["US", "EP"],
        )

    def test_search_parses_traceable_patent_hits(self) -> None:
        provider = GooglePatentsProvider(self.settings, http_getter=self.getter)
        hits = asyncio.run(provider.search(self.query()))
        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[0].publication_number, "US10893120B2")
        self.assertEqual(hits[0].title, "Data caching and data-aware placement")
        self.assertEqual(hits[0].assignee, "International Business Machines Corp")
        self.assertIn("cache & locality", hits[0].snippet)
        self.assertEqual(hits[1].publication_number, "EP0965918A2")
        self.assertEqual(hits[1].provider_rank, 2)
        self.assertTrue(hits[1].url.startswith("https://patents.google.com/patent/"))

    def test_url_encodes_query_limit_country_and_page(self) -> None:
        provider = GooglePatentsProvider(self.settings, http_getter=self.getter)
        url = provider.build_search_url(self.query(round_number=3, limit=25))
        self.assertIn("/xhr/query?url=", url)
        self.assertIn("q%3Dcache%2Baffinity%2Bscheduling", url)
        self.assertIn("num%3D25", url)
        self.assertIn("page%3D2", url)
        self.assertIn("country%3DUS%252CEP", url)

    def test_search_honors_requested_limit(self) -> None:
        provider = GooglePatentsProvider(self.settings, http_getter=self.getter)
        hits = asyncio.run(provider.search(self.query(limit=1)))
        self.assertEqual(len(hits), 1)

    def test_response_cache_avoids_second_network_call(self) -> None:
        root = Path(self.temp.name)
        database = Database(root / "idea.db")
        database.initialize()
        cache = CacheStore(
            root / "cache", database, max_bytes=1024 * 1024, low_watermark_bytes=900_000
        )
        provider = GooglePatentsProvider(
            self.settings, cache=cache, http_getter=self.getter
        )
        asyncio.run(provider.search(self.query()))
        asyncio.run(provider.search(self.query()))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(cache.stats()["entry_count"], 1)

    def test_network_failure_is_not_reported_as_success(self) -> None:
        async def failing_getter(url: str) -> bytes:
            raise ConnectionError("offline")

        provider = GooglePatentsProvider(self.settings, http_getter=failing_getter)
        result = asyncio.run(
            ProviderRunner().search(provider, self.query(), timeout_seconds=1)
        )
        self.assertEqual(result.status, ProviderStatus.ERROR)
        self.assertFalse(result.succeeded)
        self.assertEqual(result.hits, [])


if __name__ == "__main__":
    unittest.main()
