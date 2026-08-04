from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from idea.cache import CacheStore
from idea.config import load_config
from idea.database import Database
from idea.providers import (
    FetchRequest,
    PageStopReason,
    PagedSearchProvider,
    ProviderRunner,
    ProviderStatus,
    SearchQuery,
    SerpApiPatentProvider,
    parse_serpapi_patent_details,
)


DETAILS = {
    "search_metadata": {
        "google_patents_details_url": "https://patents.google.com/patent/US123A1/en"
    },
    "type": "patent",
    "title": "Liquid cooled server rack",
    "publication_number": "US123A1",
    "application_number": "US18/123,456",
    "assignees": ["Example Corp"],
    "inventors": [{"name": "Ada Example"}],
    "priority_date": "2025-01-01",
    "filing_date": "2025-02-01",
    "publication_date": "2026-06-01",
    "family_id": "family-123",
    "abstract": "A liquid cooling system for a server rack.",
    "claims": [
        "1. A server rack comprising a liquid cooling loop.",
        "2. The server rack of claim 1 comprising a cold plate.",
    ],
    "worldwide_applications": {
        "2025": [{"country_code": "US", "application_number": "US18/123,456"}]
    },
    "description_link": "https://serpapi.com/searches/example/description.html",
}


class SerpApiProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.key = "fixture-serpapi-secret"
        self.credentials = self.root / "provider-credentials.local.json"
        self.credentials.write_text(
            json.dumps({"serpapi": {"api_key": self.key}}), encoding="utf-8"
        )
        self.settings = load_config().search.providers.serpapi_google_patents.model_copy(
            update={"api_key_file": self.credentials, "max_attempts": 1}
        )
        self.calls: list[dict] = []

        async def transport(arguments):
            self.calls.append(arguments)
            if arguments["engine"] == self.settings.details_engine:
                return DETAILS
            return {
                "organic_results": [
                    {
                        "position": 1,
                        "patent_id": "patent/US123A1/en",
                        "patent_link": "https://patents.google.com/patent/US123A1/en",
                        "title": "Liquid cooled server rack",
                        "snippet": "Cold plate and coolant loop.",
                        "priority_date": "2025-01-01",
                        "filing_date": "2025-02-01",
                        "publication_date": "2026-06-01",
                        "assignee": "Example Corp",
                        "publication_number": "US123A1",
                        "language": "en",
                        "country_status": {"US": "ACTIVE"},
                    }
                ]
            }

        async def description(_url):
            return "Existing racks use inefficient air cooling.\n\nThe cold plate removes heat."

        self.transport = transport
        self.description = description

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def query() -> SearchQuery:
        return SearchQuery(
            query_id="Q-SERP-1",
            text="liquid cooling after=publication:20260401 before=publication:20260701",
            round_number=1,
            limit=5,
            query_type="technical_means",
        )

    def provider(self, *, cache=None, transport=None) -> SerpApiPatentProvider:
        return SerpApiPatentProvider(
            self.settings,
            cache=cache,
            transport=transport or self.transport,
            description_transport=self.description,
        )

    def test_search_uses_structured_window_and_maps_patent_fields(self) -> None:
        provider = self.provider()
        self.assertIsInstance(provider, PagedSearchProvider)
        hits = asyncio.run(provider.search(self.query()))
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].provider, "serpapi_google_patents")
        self.assertEqual(hits[0].publication_number, "US123A1")
        self.assertEqual(hits[0].publication_date, "2026-06-01")
        self.assertEqual(hits[0].assignee, "Example Corp")
        arguments = self.calls[0]
        self.assertEqual(arguments["engine"], "google_patents")
        self.assertEqual(arguments["q"], "liquid cooling")
        self.assertEqual(arguments["after"], "publication:20260401")
        self.assertEqual(arguments["before"], "publication:20260701")
        self.assertEqual(arguments["num"], 10)
        self.assertEqual(arguments["api_key"], self.key)

    def test_assignee_expression_uses_structured_parameter(self) -> None:
        query = self.query().model_copy(
            update={
                "text": (
                    'assignee:"华为" OR assignee:"Huawei Technologies Co., Ltd." '
                    "after=publication:20250721 before=publication:20260723"
                )
            }
        )
        asyncio.run(self.provider().search(query))
        arguments = self.calls[0]
        self.assertEqual(arguments["q"], "华为")
        self.assertEqual(
            arguments["assignee"], "华为,(Huawei Technologies Co., Ltd.)"
        )
        self.assertNotIn("assignee:", arguments["q"])

    def test_no_results_payload_is_a_successful_empty_result(self) -> None:
        async def no_results(_arguments):
            return {"error": "Google Patents hasn't returned any results for this query."}

        result = asyncio.run(
            ProviderRunner().search(
                self.provider(transport=no_results), self.query(), timeout_seconds=1
            )
        )
        self.assertEqual(result.status, ProviderStatus.EMPTY)
        self.assertEqual(result.hits, [])
        self.assertIsNone(result.error_code)

    def test_search_page_consumes_provider_cursor_and_reports_totals(self) -> None:
        async def paged(arguments):
            self.calls.append(arguments)
            page = arguments.get("page", 1)
            numbers = range(1, 11) if page == 1 else range(11, 13)
            results = [
                {
                    "position": number,
                    "patent_link": f"https://patents.google.com/patent/US{number}A1/en",
                    "publication_number": f"US{number}A1",
                }
                for number in numbers
            ]
            payload = {
                "search_metadata": {"id": f"request-{page}"},
                "search_information": {"total_results": "12"},
                "organic_results": results,
            }
            if page == 1:
                payload["serpapi_pagination"] = {
                    "next": "https://serpapi.com/search.json?q=x&page=2"
                }
            return payload

        provider = self.provider(transport=paged)
        first = asyncio.run(provider.search_page(self.query(), None))
        self.assertEqual(first.page_number, 1)
        self.assertEqual(first.next_cursor, "page:2")
        self.assertEqual(first.reported_total_results, 12)
        self.assertEqual(first.reported_total_pages, 2)
        self.assertEqual(first.provider_request_id, "request-1")
        self.assertEqual(first.stop_reason, PageStopReason.MORE_AVAILABLE)

        second = asyncio.run(provider.search_page(self.query(), first.next_cursor))
        self.assertEqual(self.calls[-1]["page"], 2)
        self.assertEqual(second.page_number, 2)
        self.assertIsNone(second.next_cursor)
        self.assertEqual(second.provider_request_id, "request-2")
        self.assertEqual(second.stop_reason, PageStopReason.QUERY_EXHAUSTED)

    def test_missing_next_link_with_unread_total_is_explicit_hard_limit(self) -> None:
        async def limited(_arguments):
            return {
                "search_metadata": {"id": "limited-request"},
                "search_information": {"total_results": 500},
                "organic_results": [
                    {
                        "patent_link": f"https://patents.google.com/patent/US{i}A1/en",
                        "publication_number": f"US{i}A1",
                    }
                    for i in range(1, 11)
                ],
            }

        page = asyncio.run(self.provider(transport=limited).search_page(self.query(), None))
        self.assertEqual(page.stop_reason, PageStopReason.PROVIDER_HARD_LIMIT)
        self.assertIsNone(page.next_cursor)

    def test_invalid_or_non_advancing_cursor_fails_before_provider_call(self) -> None:
        provider = self.provider()
        for cursor in ("2", "page:1", "page:0", "page:2?api_key=secret"):
            with self.subTest(cursor=cursor), self.assertRaisesRegex(ValueError, "cursor"):
                asyncio.run(provider.search_page(self.query(), cursor))
        self.assertEqual(self.calls, [])

    def test_details_map_claims_description_family_and_offsets(self) -> None:
        request = FetchRequest(request_id="F-SERP-1", publication_number="US123A1")
        result = asyncio.run(
            ProviderRunner().fetch(self.provider(), request, timeout_seconds=1)
        )
        self.assertEqual(result.status, ProviderStatus.SUCCESS)
        document = result.document
        self.assertEqual(document.application_number, "US18/123,456")
        self.assertEqual(document.family_id, "family-123")
        self.assertEqual(document.assignee, "Example Corp")
        self.assertEqual(document.assignees, ["Example Corp"])
        self.assertIn("claim 1", document.section_spans["claims"][0]["label"])
        self.assertIn("inefficient air cooling", document.description_text)
        span = document.section_spans["claims"][1]
        self.assertEqual(document.claims_text[span["start"] : span["end"]], span["text"])
        self.assertIn("worldwide_applications", document.raw_metadata)

    def test_abstract_only_fetch_does_not_request_description(self) -> None:
        description_calls = []

        async def description(url):
            description_calls.append(url)
            return "must not be fetched"

        provider = SerpApiPatentProvider(
            self.settings,
            transport=self.transport,
            description_transport=description,
        )
        request = FetchRequest(
            request_id="F-SERP-ABSTRACT",
            publication_number="US123A1",
            include_description=False,
        )
        result = asyncio.run(ProviderRunner().fetch(provider, request, timeout_seconds=1))
        self.assertEqual(result.status, ProviderStatus.SUCCESS)
        self.assertEqual(description_calls, [])
        self.assertEqual(result.document.description_text, "")

    def test_missing_local_key_fails_once_and_does_not_echo_secret(self) -> None:
        self.credentials.write_text(
            json.dumps({"serpapi": {"api_key": ""}}), encoding="utf-8"
        )
        result = asyncio.run(
            ProviderRunner().search(self.provider(), self.query(), timeout_seconds=1)
        )
        self.assertEqual(result.status, ProviderStatus.ERROR)
        self.assertEqual(result.error_code, "SERPAPI_API_KEY_REQUIRED")
        self.assertNotIn(self.key, result.error_message)
        self.assertEqual(self.calls, [])

    def test_api_error_redacts_key_from_provider_result(self) -> None:
        async def failing(arguments):
            return {"error": f"Invalid API key {arguments['api_key']}"}

        result = asyncio.run(
            ProviderRunner().search(
                self.provider(transport=failing), self.query(), timeout_seconds=1
            )
        )
        self.assertEqual(result.error_code, "SERPAPI_AUTH_ERROR")
        self.assertNotIn(self.key, result.error_message)
        self.assertIn("[REDACTED]", result.error_message)

    def test_cache_key_and_file_do_not_contain_api_key(self) -> None:
        database = Database(self.root / "idea.db")
        database.initialize()
        cache = CacheStore(
            self.root / "cache",
            database,
            max_bytes=1_000_000,
            low_watermark_bytes=900_000,
        )
        provider = self.provider(cache=cache)
        asyncio.run(provider.search(self.query()))
        asyncio.run(provider.search(self.query()))
        self.assertEqual(len(self.calls), 1)
        entries = cache.entries()
        self.assertEqual(len(entries), 1)
        self.assertNotIn(self.key, entries[0]["cache_key"])
        self.assertNotIn(
            self.key,
            Path(entries[0]["path"]).read_text(encoding="utf-8"),
        )

    def test_parser_rejects_scholar_or_empty_patent(self) -> None:
        with self.assertRaisesRegex(ValueError, "not a patent"):
            parse_serpapi_patent_details(
                {"type": "scholar"}, requested_publication="US1A1", language="en"
            )
        with self.assertRaisesRegex(ValueError, "no usable patent text"):
            parse_serpapi_patent_details(
                {"type": "patent"}, requested_publication="US1A1", language="en"
            )


if __name__ == "__main__":
    unittest.main()
