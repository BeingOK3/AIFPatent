from __future__ import annotations

import asyncio
import unittest

from idea.providers import (
    FetchRequest,
    FetchedDocument,
    ProviderRunner,
    ProviderStatus,
    SearchHit,
    SearchProvider,
    SearchQuery,
)


def query(limit: int = 10) -> SearchQuery:
    return SearchQuery(
        query_id="Q1",
        text="cache scheduling patent",
        round_number=1,
        limit=limit,
        query_type="technical_means",
    )


class FakeProvider(SearchProvider):
    name = "fake"

    def __init__(self, hits=None, delay=0, error=None):
        self.hits = hits if hits is not None else []
        self.delay = delay
        self.error = error

    async def search(self, request):
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.hits

    async def fetch(self, request):
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return FetchedDocument(
            provider=self.name,
            publication_number=request.publication_number or "US1A1",
            url=request.url or "https://example.test/US1A1",
        )


class ProviderContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = ProviderRunner()

    def test_real_hits_produce_success(self) -> None:
        provider = FakeProvider(
            [
                SearchHit(
                    provider="fake",
                    provider_rank=1,
                    title="Patent",
                    url="https://example.test/patent",
                    publication_number="US1A1",
                )
            ]
        )
        result = asyncio.run(self.runner.search(provider, query(), timeout_seconds=1))
        self.assertEqual(result.status, ProviderStatus.SUCCESS)
        self.assertTrue(result.succeeded)
        self.assertEqual(len(result.hits), 1)

    def test_empty_is_a_real_successful_call_not_a_hit(self) -> None:
        result = asyncio.run(
            self.runner.search(FakeProvider(), query(), timeout_seconds=1)
        )
        self.assertEqual(result.status, ProviderStatus.EMPTY)
        self.assertTrue(result.succeeded)
        self.assertEqual(result.hits, [])

    def test_timeout_cannot_be_reported_as_success(self) -> None:
        result = asyncio.run(
            self.runner.search(FakeProvider(delay=0.05), query(), timeout_seconds=0.001)
        )
        self.assertEqual(result.status, ProviderStatus.TIMEOUT)
        self.assertFalse(result.succeeded)
        self.assertEqual(result.hits, [])

    def test_exception_is_auditable_error(self) -> None:
        result = asyncio.run(
            self.runner.search(
                FakeProvider(error=RuntimeError("service down")),
                query(),
                timeout_seconds=1,
            )
        )
        self.assertEqual(result.status, ProviderStatus.ERROR)
        self.assertEqual(result.error_code, "RuntimeError")
        self.assertFalse(result.succeeded)

    def test_wrong_provider_or_duplicate_rank_fails_contract(self) -> None:
        hits = [
            SearchHit(provider="other", provider_rank=1, url="https://example.test/1"),
            SearchHit(provider="other", provider_rank=1, url="https://example.test/2"),
        ]
        result = asyncio.run(
            self.runner.search(FakeProvider(hits), query(), timeout_seconds=1)
        )
        self.assertEqual(result.status, ProviderStatus.CONTRACT_ERROR)
        self.assertEqual(result.hits, [])

    def test_more_results_than_limit_fails_contract(self) -> None:
        hits = [
            SearchHit(provider="fake", provider_rank=i + 1, url=f"https://example.test/{i}")
            for i in range(2)
        ]
        result = asyncio.run(
            self.runner.search(FakeProvider(hits), query(limit=1), timeout_seconds=1)
        )
        self.assertEqual(result.status, ProviderStatus.CONTRACT_ERROR)

    def test_fetch_requires_real_validated_document(self) -> None:
        request = FetchRequest(request_id="F1", publication_number="US1A1")
        result = asyncio.run(
            self.runner.fetch(FakeProvider(), request, timeout_seconds=1)
        )
        self.assertEqual(result.status, ProviderStatus.SUCCESS)
        self.assertEqual(result.document.publication_number, "US1A1")

    def test_disabled_result_is_explicit(self) -> None:
        result = self.runner.disabled("fake", "search", "Q1")
        self.assertEqual(result.status, ProviderStatus.DISABLED)
        self.assertFalse(result.succeeded)


if __name__ == "__main__":
    unittest.main()
