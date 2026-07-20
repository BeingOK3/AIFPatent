from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from idea.cache import CacheStore
from idea.config import load_config
from idea.database import Database
from idea.providers import (
    FetchRequest,
    GooglePatentsProvider,
    ProviderRunner,
    ProviderStatus,
    parse_patent_html,
)


FIXTURE = Path("backend/tests/fixtures/google_patent_detail.html").read_bytes()


class GooglePatentsFetchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.settings = load_config().search.providers.google_patents_local.model_copy(
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
            return FIXTURE

        self.getter = getter

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_parser_extracts_metadata_and_full_text_sections(self) -> None:
        document = parse_patent_html(
            FIXTURE.decode(),
            provider="google_patents_local",
            url="https://patents.google.com/patent/US10893120B2/en",
            language="en",
        )
        self.assertEqual(document.publication_number, "US10893120B2")
        self.assertEqual(document.application_number, "US15/999,001")
        self.assertEqual(document.assignee, "Example Research Corp")
        self.assertEqual(document.inventors, ["Ada Inventor"])
        self.assertEqual(document.priority_date, "2017-08-15")
        self.assertIn("cache and data locality", document.abstract_text)
        self.assertIn("The method of claim 1", document.claims_text)
        self.assertIn("Existing systems", document.description_text)
        self.assertEqual(document.section_spans["claims"][0]["label"], "claim 1")
        self.assertEqual(document.section_spans["description"][1]["label"], "[0002]")
        span = document.section_spans["claims"][0]
        self.assertEqual(
            document.claims_text[span["start"] : span["end"]], span["text"]
        )

    def test_fetch_builds_canonical_url_from_publication_number(self) -> None:
        provider = GooglePatentsProvider(self.settings, http_getter=self.getter)
        request = FetchRequest(request_id="F1", publication_number="US 10893120 B2")
        result = asyncio.run(ProviderRunner().fetch(provider, request, timeout_seconds=1))
        self.assertEqual(result.status, ProviderStatus.SUCCESS)
        self.assertEqual(result.document.publication_number, "US10893120B2")
        self.assertTrue(self.calls[0].endswith("/patent/US10893120B2/en"))

    def test_document_response_uses_fifo_cache(self) -> None:
        root = Path(self.temp.name)
        database = Database(root / "idea.db")
        database.initialize()
        cache = CacheStore(
            root / "cache", database, max_bytes=1024 * 1024, low_watermark_bytes=900_000
        )
        provider = GooglePatentsProvider(
            self.settings, cache=cache, http_getter=self.getter
        )
        request = FetchRequest(request_id="F1", publication_number="US10893120B2")
        asyncio.run(provider.fetch(request))
        asyncio.run(provider.fetch(request))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(cache.entries()[0]["category"], "documents")

    def test_invalid_page_is_contract_error(self) -> None:
        async def invalid_getter(url: str) -> bytes:
            return b"<html><body>consent or changed page</body></html>"

        provider = GooglePatentsProvider(self.settings, http_getter=invalid_getter)
        request = FetchRequest(request_id="F1", publication_number="US10893120B2")
        result = asyncio.run(ProviderRunner().fetch(provider, request, timeout_seconds=1))
        self.assertEqual(result.status, ProviderStatus.CONTRACT_ERROR)
        self.assertIsNone(result.document)

    def test_mismatched_publication_is_contract_error(self) -> None:
        provider = GooglePatentsProvider(self.settings, http_getter=self.getter)
        request = FetchRequest(request_id="F1", publication_number="US99999999B2")
        result = asyncio.run(ProviderRunner().fetch(provider, request, timeout_seconds=1))
        self.assertEqual(result.status, ProviderStatus.CONTRACT_ERROR)
        self.assertIn("does not match", result.error_message)


if __name__ == "__main__":
    unittest.main()
