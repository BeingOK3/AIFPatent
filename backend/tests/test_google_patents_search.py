from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

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
            update={
                "min_request_interval_seconds": 0,
                "search_interval_min_seconds": 0,
                "search_interval_max_seconds": 0,
                "document_interval_min_seconds": 0,
                "document_interval_max_seconds": 0,
                "max_attempts": 1,
            }
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

    def test_all_provider_instances_share_one_domain_request_lock(self) -> None:
        active = 0
        maximum_active = 0

        async def getter(url: str) -> bytes:
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            await asyncio.sleep(0.01)
            active -= 1
            return JSON_FIXTURE

        async def run() -> None:
            left = GooglePatentsProvider(self.settings, http_getter=getter)
            right = GooglePatentsProvider(self.settings, http_getter=getter)
            await asyncio.gather(
                left.search(self.query()),
                right.search(self.query()),
            )

        asyncio.run(run())
        self.assertEqual(maximum_active, 1)

    def test_network_errors_retry_twice_with_backoff(self) -> None:
        calls = 0

        async def failing_getter(url: str) -> bytes:
            nonlocal calls
            calls += 1
            raise ConnectionError("offline")

        root = Path(self.temp.name)
        database = Database(root / "network-circuit.db")
        database.initialize()
        cache = CacheStore(
            root / "network-circuit-cache",
            database,
            max_bytes=1024 * 1024,
            low_watermark_bytes=900_000,
        )
        settings = self.settings.model_copy(update={"max_attempts": 3})
        provider = GooglePatentsProvider(
            settings, cache=cache, http_getter=failing_getter
        )
        with patch(
            "idea.providers.google_patents.asyncio.sleep", new_callable=AsyncMock
        ) as sleep:
            result = asyncio.run(
                ProviderRunner().search(provider, self.query(), timeout_seconds=1)
            )
        self.assertEqual(result.status, ProviderStatus.ERROR)
        self.assertEqual(calls, 3)
        self.assertEqual(sleep.await_count, 2)
        with database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM provider_circuit_breakers WHERE provider = ?",
                ("google_patents_local",),
            ).fetchone()
        self.assertEqual(row["error_code"], "GOOGLE_PATENTS_TEMPORARILY_UNAVAILABLE")

    def test_invalid_json_is_not_retried(self) -> None:
        calls = 0

        async def invalid_getter(url: str) -> bytes:
            nonlocal calls
            calls += 1
            return b"not-json"

        settings = self.settings.model_copy(update={"max_attempts": 3})
        provider = GooglePatentsProvider(settings, http_getter=invalid_getter)
        result = asyncio.run(
            ProviderRunner().search(provider, self.query(), timeout_seconds=1)
        )
        self.assertEqual(result.status, ProviderStatus.CONTRACT_ERROR)
        self.assertEqual(calls, 1)

    def test_google_sorry_page_opens_persistent_circuit_without_retry(self) -> None:
        root = Path(self.temp.name)
        database = Database(root / "circuit.db")
        database.initialize()
        cache = CacheStore(
            root / "circuit-cache",
            database,
            max_bytes=1024 * 1024,
            low_watermark_bytes=900_000,
        )
        settings = self.settings.model_copy(
            update={
                "max_attempts": 3,
                "blocked_cooldown_min_seconds": 1800,
                "blocked_cooldown_max_seconds": 1800,
            }
        )
        response = MagicMock()
        response.status_code = 503
        response.headers = {"content-type": "text/html; charset=UTF-8"}
        response.content = b"<html><head><title>Sorry...</title></head></html>"

        with patch("idea.providers.google_patents.httpx.AsyncClient") as client_class:
            client = client_class.return_value.__aenter__.return_value
            client.get = AsyncMock(return_value=response)
            first = asyncio.run(
                ProviderRunner().search(
                    GooglePatentsProvider(settings, cache=cache),
                    self.query(),
                    timeout_seconds=1,
                )
            )

        self.assertEqual(first.status, ProviderStatus.ERROR)
        self.assertEqual(first.error_code, "GOOGLE_PATENTS_BLOCKED")
        self.assertEqual(client.get.await_count, 1)
        with database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM provider_circuit_breakers WHERE provider = ?",
                ("google_patents_local",),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["error_code"], "GOOGLE_PATENTS_BLOCKED")

        async def should_not_call(url: str) -> bytes:
            raise AssertionError("open circuit must prevent network calls")

        second = asyncio.run(
            ProviderRunner().search(
                GooglePatentsProvider(settings, cache=cache, http_getter=should_not_call),
                self.query(),
                timeout_seconds=1,
            )
        )
        self.assertEqual(second.status, ProviderStatus.ERROR)
        self.assertEqual(second.error_code, "GOOGLE_PATENTS_BLOCKED")

    def test_plain_503_is_retried_only_once(self) -> None:
        settings = self.settings.model_copy(update={"max_attempts": 3})
        response = MagicMock()
        response.status_code = 503
        response.headers = {"content-type": "application/json"}
        response.content = b"{}"
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "503",
            request=httpx.Request("GET", "https://patents.google.com/xhr/query"),
            response=httpx.Response(503),
        )
        with (
            patch("idea.providers.google_patents.httpx.AsyncClient") as client_class,
            patch(
                "idea.providers.google_patents.asyncio.sleep", new_callable=AsyncMock
            ),
        ):
            client = client_class.return_value.__aenter__.return_value
            client.get = AsyncMock(return_value=response)
            result = asyncio.run(
                ProviderRunner().search(
                    GooglePatentsProvider(settings), self.query(), timeout_seconds=1
                )
            )
        self.assertEqual(result.status, ProviderStatus.ERROR)
        self.assertEqual(client.get.await_count, 2)

    def test_429_obeys_retry_after_and_opens_persistent_circuit(self) -> None:
        root = Path(self.temp.name)
        database = Database(root / "rate-limit.db")
        database.initialize()
        cache = CacheStore(
            root / "rate-limit-cache",
            database,
            max_bytes=1024 * 1024,
            low_watermark_bytes=900_000,
        )
        response = MagicMock()
        response.status_code = 429
        response.headers = {
            "content-type": "text/html; charset=UTF-8",
            "retry-after": "120",
        }
        response.content = b"<html><head><title>Sorry...</title></head></html>"
        before_ms = int(time.time() * 1000)
        with patch("idea.providers.google_patents.httpx.AsyncClient") as client_class:
            client = client_class.return_value.__aenter__.return_value
            client.get = AsyncMock(return_value=response)
            result = asyncio.run(
                ProviderRunner().search(
                    GooglePatentsProvider(self.settings, cache=cache),
                    self.query(),
                    timeout_seconds=1,
                )
            )
        self.assertEqual(result.error_code, "GOOGLE_PATENTS_RATE_LIMITED")
        with database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM provider_circuit_breakers WHERE provider = ?",
                ("google_patents_local",),
            ).fetchone()
        self.assertGreaterEqual(row["blocked_until"], before_ms + 119_000)


if __name__ == "__main__":
    unittest.main()
