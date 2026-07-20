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
    ExaMcpProvider,
    FetchRequest,
    McpHttpClient,
    ProviderRunner,
    ProviderStatus,
    SearchQuery,
    parse_exa_patent_markdown,
)


PATENT_MARKDOWN = """# US8816648B2 - Adaptive battery charging - Google Patents

## Info

Publication number US8816648B2 Application number US12/542,411 Prior art date 2009-08-17 Current Assignee (The listed assignees may be inaccurate.) Apple Inc Original Assignee Apple Inc Priority date (not a legal conclusion) 2009-08-17 Filing date 2009-08-17 Publication date 2014-08-26

## Abstract

Charge a battery using temperature-dependent current and voltage stages.

## Description

The controller measures battery temperature.

It chooses a charging table for the measured range.

## Claims (2)

1. A charging method comprising measuring battery temperature and selecting charging stages.

2. The method of claim 1, wherein the battery is a lithium battery.

## Publications (2)

| Publication Number | Publication Date |
| US8816648B2 | 2014-08-26 |
"""


class McpProtocolTests(unittest.TestCase):
    def test_initialize_notification_and_tool_call_share_session(self) -> None:
        calls = []

        async def transport(payload, headers):
            calls.append((payload, headers))
            if payload["method"] == "initialize":
                return {
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {"protocolVersion": "2025-03-26", "capabilities": {}},
                }, {"mcp-session-id": "session-1"}
            if payload["method"] == "notifications/initialized":
                return {}, {}
            return {
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {"content": [{"type": "text", "text": "ok"}]},
            }, {}

        client = McpHttpClient("https://mcp.example.test", timeout_seconds=1, transport=transport)
        result = asyncio.run(client.call_tool("web_search_exa", {"query": "patent"}))
        self.assertEqual(result["content"][0]["text"], "ok")
        self.assertEqual([item[0]["method"] for item in calls], [
            "initialize", "notifications/initialized", "tools/call"
        ])
        self.assertEqual(calls[1][1]["Mcp-Session-Id"], "session-1")
        self.assertEqual(calls[2][0]["params"]["name"], "web_search_exa")

    def test_sse_decoder_extracts_json_data(self) -> None:
        payload = b'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{}}\n\n'
        result = McpHttpClient._decode(payload, "text/event-stream")
        self.assertEqual(result["id"], 1)


class ExaProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.settings = load_config().search.providers.exa_mcp.model_copy(
            update={"max_attempts": 1}
        )
        self.calls = []

        async def caller(tool, arguments):
            self.calls.append((tool, arguments))
            if tool == self.settings.search_tool:
                return {
                    "structuredContent": {
                        "results": [
                            {
                                "title": "Cache affinity based scheduling",
                                "url": "https://patents.google.com/patent/EP0965918A2/en",
                                "text": "Measure cache footprint and thread affinity.",
                                "publishedDate": "1999-12-22",
                            },
                            {
                                "title": "Duplicate family result",
                                "url": "https://patents.google.com/patent/EP0965918A2/en",
                                "text": "duplicate",
                            },
                        ]
                    }
                }
            return {"content": [{"type": "text", "text": "Fetched patent content"}]}

        self.caller = caller

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def query():
        return SearchQuery(
            query_id="Q-EXA-1",
            text="cache scheduling",
            round_number=1,
            limit=10,
            query_type="technical_means",
        )

    def test_structured_search_results_are_normalized_and_deduplicated(self) -> None:
        provider = ExaMcpProvider(self.settings, caller=self.caller)
        hits = asyncio.run(provider.search(self.query()))
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].publication_number, "EP0965918A2")
        self.assertEqual(hits[0].provider, "exa_mcp")
        self.assertIn("site:patents.google.com/patent", self.calls[0][1]["query"])

    def test_json_text_result_is_supported(self) -> None:
        async def json_caller(tool, arguments):
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps({"results": [{
                            "title": "Patent", "url": "https://patents.google.com/patent/US1A1/en"
                        }]}),
                    }
                ]
            }

        hits = asyncio.run(ExaMcpProvider(self.settings, caller=json_caller).search(self.query()))
        self.assertEqual(hits[0].publication_number, "US1A1")

    def test_fetch_returns_traceable_fallback_document(self) -> None:
        provider = ExaMcpProvider(self.settings, caller=self.caller)
        request = FetchRequest(request_id="F-EXA-1", publication_number="EP0965918A2")
        result = asyncio.run(ProviderRunner().fetch(provider, request, timeout_seconds=1))
        self.assertEqual(result.status, ProviderStatus.SUCCESS)
        self.assertIn("Fetched patent content", result.document.description_text)
        self.assertFalse(result.document.raw_metadata["structured_sections"])
        self.assertEqual(self.calls[-1][1]["urls"], ["https://patents.google.com/patent/EP0965918A2/en"])
        self.assertEqual(
            self.calls[-1][1]["maxCharacters"], self.settings.fetch_max_characters
        )

    def test_google_patents_markdown_is_split_into_metadata_and_evidence_sections(self) -> None:
        document = parse_exa_patent_markdown(
            PATENT_MARKDOWN,
            provider="exa_mcp",
            publication_number="US8816648B2",
            url="https://patents.google.com/patent/US8816648B2/en",
            language="en",
        )
        self.assertEqual(document.title, "Adaptive battery charging")
        self.assertEqual(document.application_number, "US12/542,411")
        self.assertEqual(document.assignee, "Apple Inc")
        self.assertEqual(document.priority_date, "2009-08-17")
        self.assertEqual(document.publication_date, "2014-08-26")
        self.assertIn("temperature-dependent", document.abstract_text)
        self.assertIn("2. The method", document.claims_text)
        self.assertEqual(len(document.section_spans["claims"]), 2)
        self.assertEqual(document.section_spans["claims"][0]["label"], "claim 1")
        claim_span = document.section_spans["claims"][1]
        self.assertEqual(
            document.claims_text[claim_span["start"] : claim_span["end"]],
            claim_span["text"],
        )
        self.assertTrue(document.raw_metadata["structured_sections"])

    def test_compact_exa_markdown_without_heading_or_metadata_spaces_is_supported(self) -> None:
        compact = PATENT_MARKDOWN.replace(
            "\n\n## Info\n\nPublication number ", ")## Info\nPublication number"
        ).replace(" Application number ", "Application number").replace(
            " Priority date ", "Priority date"
        ).replace(" Filing date ", "Filing date").replace(
            " Publication date ", "Publication date"
        )
        document = parse_exa_patent_markdown(
            compact,
            provider="exa_mcp",
            publication_number="US8816648B2",
            url="https://patents.google.com/patent/US8816648B2/en",
            language="en",
        )
        self.assertEqual(document.application_number, "US12/542,411")
        self.assertEqual(document.priority_date, "2009-08-17")
        self.assertEqual(document.publication_date, "2014-08-26")
        self.assertEqual(len(document.section_spans["claims"]), 2)

    def test_metadata_survives_when_exa_truncates_before_text_sections(self) -> None:
        truncated = """PDF)## Info
Publication numberUS11397216B2Application numberUS16/183,559Other versionsUS20190072618A1 InventorAda Current Assignee (may be inaccurate.) Qnovo Inc Original AssigneeQnovo IncPriority date (not legal)2010-05-21Filing date2018-11-07Publication date2022-07-26
"""
        document = parse_exa_patent_markdown(
            truncated,
            provider="exa_mcp",
            publication_number="US11397216B2",
            url="https://patents.google.com/patent/US11397216B2/en",
            language="en",
        )
        self.assertEqual(document.application_number, "US16/183,559")
        self.assertEqual(document.publication_date, "2022-07-26")
        self.assertFalse(document.raw_metadata["structured_sections"])
        self.assertIn("Publication number", document.description_text)

    def test_mcp_failure_is_not_success(self) -> None:
        async def failing(tool, arguments):
            raise ConnectionError("MCP down")

        provider = ExaMcpProvider(self.settings, caller=failing)
        result = asyncio.run(
            ProviderRunner().search(provider, self.query(), timeout_seconds=1)
        )
        self.assertEqual(result.status, ProviderStatus.ERROR)
        self.assertFalse(result.succeeded)

    def test_tool_response_cache_prevents_duplicate_call(self) -> None:
        root = Path(self.temp.name)
        database = Database(root / "idea.db")
        database.initialize()
        cache = CacheStore(root / "cache", database, max_bytes=1_000_000, low_watermark_bytes=900_000)
        provider = ExaMcpProvider(self.settings, cache=cache, caller=self.caller)
        asyncio.run(provider.search(self.query()))
        asyncio.run(provider.search(self.query()))
        self.assertEqual(len(self.calls), 1)


if __name__ == "__main__":
    unittest.main()
