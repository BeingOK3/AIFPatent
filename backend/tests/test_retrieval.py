from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import date
from pathlib import Path

from idea.agent_schemas import QueryPlannerOutput
from idea.config import load_config
from idea.database import Database
from idea.providers import FetchRequest, FetchedDocument, SearchHit, SearchProvider
from idea.retrieval import RetrievalService
from idea.search_strategy import ScopeBreadth, StopReason, build_budget


class FakeProvider(SearchProvider):
    def __init__(self, name, *, fail_search=False, fail_fetch=False, hit_count=10):
        self.name = name
        self.fail_search = fail_search
        self.fail_fetch = fail_fetch
        self.hit_count = hit_count
        self.search_calls = 0
        self.fetch_calls = 0

    async def search(self, query):
        self.search_calls += 1
        if self.fail_search:
            raise ConnectionError("search offline")
        return [
            SearchHit(
                provider=self.name,
                provider_rank=index + 1,
                title=f"cache eviction token heat patent {index}",
                url=f"https://patents.google.com/patent/US{index + 1}A1/en",
                publication_number=f"US{index + 1}A1",
                snippet="cache eviction based on token heat threshold",
                publication_date="2020-01-01",
            )
            for index in range(self.hit_count)
        ]

    async def fetch(self, request: FetchRequest):
        self.fetch_calls += 1
        if self.fail_fetch:
            raise ConnectionError("fetch offline")
        publication = request.publication_number
        return FetchedDocument(
            provider=self.name,
            publication_number=publication,
            title=f"Patent {publication}",
            url=request.url or f"https://patents.google.com/patent/{publication}/en",
            abstract_text="cache eviction by token heat",
            claims_text="1. cache eviction using a token heat threshold",
            description_text="[0001] cache controller details",
            section_spans={"claims": [{"label": "claim 1", "start": 0, "end": 46, "text": "1. cache eviction using a token heat threshold"}]},
        )


class MissingIdentifierProvider(FakeProvider):
    async def search(self, query):
        hits = await super().search(query)
        hits[-1] = SearchHit(
            provider=self.name,
            provider_rank=len(hits),
            title="cache eviction token heat patent without identifier",
            url="https://example.test/patent-result-without-publication-number",
            snippet="cache eviction based on token heat threshold",
            publication_date="2020-01-01",
        )
        return hits


def plan():
    return QueryPlannerOutput.model_validate({
        "term_groups": [
            {"concept": "cache", "zh_terms": ["缓存"], "en_terms": ["cache"]},
            {"concept": "heat", "zh_terms": ["热度"], "en_terms": ["token heat"]},
        ],
        "queries": [
            {"query_id": "Q1", "round_number": 1, "query_type": "technical_means", "language": "en", "query_text": "cache eviction token heat", "rationale": "means"},
            {"query_id": "Q2", "round_number": 1, "query_type": "problem_effect", "language": "en", "query_text": "reduce cache miss threshold", "rationale": "problem"},
        ],
    })


class RetrievalServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "idea.db")
        self.db.initialize()
        case = self.db.create_case("Retrieval")
        self.run = self.db.create_run(
            case_id=case["case_id"], input_text="idea", evaluation_date="2026-07-16",
            date_basis="default", analysis_scope="full", model="stub", skill_version="1",
            workflow_version="1", config_snapshot={},
        )
        with self.db.connect() as connection:
            for query in plan().queries:
                connection.execute(
                    "INSERT INTO search_queries VALUES(?,?,?,?,?,?,?,?)",
                    (f"{self.run['run_id']}:{query.query_id}", self.run["run_id"], query.round_number,
                     query.query_type, query.language, query.query_text, query.rationale, 1),
                )
        self.budget = build_budget(load_config().search, ScopeBreadth.NARROW, mode_name="standard")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def retrieve(self, providers):
        service = RetrievalService(
            self.db, providers, search_timeout_seconds={p.name: 1 for p in providers}
        )
        result = asyncio.run(service.retrieve(
            run_id=self.run["run_id"], plan=plan(), budget=self.budget,
            idea_terms=["cache eviction", "token heat", "threshold"],
            evaluation_date=date(2026, 7, 16), saturation_rounds=2,
            saturation_new_high_max=1,
        ))
        return service, result

    def test_two_providers_run_and_duplicates_merge_with_persisted_calls(self) -> None:
        left = FakeProvider("exa_mcp")
        right = FakeProvider("google_patents_local")
        _, result = self.retrieve([left, right])
        self.assertEqual(left.search_calls, 2)
        self.assertEqual(right.search_calls, 2)
        self.assertEqual(len(result.merged_hits), 10)
        self.assertEqual(result.merged_hits[0].found_by, ["exa_mcp", "google_patents_local"])
        with self.db.connect() as connection:
            calls = connection.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0]
            hits = connection.execute("SELECT COUNT(*) FROM search_hits").fetchone()[0]
        self.assertEqual(calls, 4)
        self.assertEqual(hits, 40)

    def test_one_provider_failure_creates_limitation_but_keeps_results(self) -> None:
        _, result = self.retrieve([
            FakeProvider("exa_mcp"), FakeProvider("google_patents_local", fail_search=True)
        ])
        self.assertEqual(len(result.merged_hits), 10)
        self.assertTrue(any(item["code"] == "PROVIDER_DEGRADED" for item in result.limitations))

    def test_all_provider_failures_are_explicit_stop(self) -> None:
        _, result = self.retrieve([
            FakeProvider("exa_mcp", fail_search=True),
            FakeProvider("google_patents_local", fail_search=True),
        ])
        self.assertEqual(result.stop_reason, StopReason.PROVIDERS_UNAVAILABLE)
        self.assertEqual(result.merged_hits, [])

    def test_fetch_falls_back_and_persists_documents(self) -> None:
        primary = FakeProvider("google_patents_local", fail_fetch=True)
        fallback = FakeProvider("exa_mcp")
        service, result = self.retrieve([primary, fallback])
        fetched = asyncio.run(service.fetch_selected(run_id=self.run["run_id"], retrieval=result))
        self.assertEqual(len(fetched.documents), 10)
        self.assertEqual(len(fetched.document_ids), 10)
        self.assertEqual(primary.fetch_calls, 10)
        self.assertEqual(fallback.fetch_calls, 10)
        with self.db.connect() as connection:
            documents = connection.execute("SELECT COUNT(*) FROM patent_documents").fetchone()[0]
            run_documents = connection.execute("SELECT COUNT(*) FROM run_documents").fetchone()[0]
        self.assertEqual(documents, 10)
        self.assertEqual(run_documents, 10)

    def test_fetch_below_minimum_has_user_facing_limitation_message(self) -> None:
        service, result = self.retrieve([FakeProvider("exa_mcp")])

        fetched = asyncio.run(service.fetch_selected(
            run_id=self.run["run_id"], retrieval=result, minimum_documents=11
        ))

        limitation = next(
            item for item in fetched.limitations
            if item["code"] == "DEEP_REVIEW_FETCHED_BELOW_MINIMUM"
        )
        self.assertEqual(limitation["fetched"], 10)
        self.assertIn("结论将明确降级", limitation["message"])

    def test_fetch_prioritizes_provider_that_succeeded_during_this_run(self) -> None:
        local = FakeProvider("google_patents_local", fail_search=True)
        exa = FakeProvider("exa_mcp")
        service, result = self.retrieve([local, exa])
        fetched = asyncio.run(
            service.fetch_selected(run_id=self.run["run_id"], retrieval=result)
        )
        self.assertEqual(len(fetched.documents), 10)
        self.assertEqual(exa.fetch_calls, 10)
        self.assertEqual(local.fetch_calls, 0)

    def test_retrieve_excludes_relevant_hit_without_publication_number(self) -> None:
        _, result = self.retrieve([MissingIdentifierProvider("exa_mcp")])
        self.assertNotIn("", result.selected_publication_numbers)
        self.assertEqual(len(result.selected_publication_numbers), 9)
        self.assertTrue(
            any(
                item["code"] == "DEEP_REVIEW_IDENTIFIER_MISSING"
                for item in result.limitations
            )
        )

    def test_fetch_skips_blank_and_duplicate_checkpoint_entries(self) -> None:
        provider = FakeProvider("exa_mcp")
        service, result = self.retrieve([provider])
        result.selected_publication_numbers.insert(3, "")
        result.selected_publication_numbers.append(result.selected_publication_numbers[0])

        fetched = asyncio.run(
            service.fetch_selected(run_id=self.run["run_id"], retrieval=result)
        )

        self.assertEqual(len(fetched.documents), 10)
        self.assertEqual(provider.fetch_calls, 10)
        codes = {item["code"] for item in fetched.limitations}
        self.assertIn("DEEP_REVIEW_IDENTIFIER_MISSING", codes)
        self.assertIn("DUPLICATE_DEEP_REVIEW_SELECTION", codes)

    def test_fetch_cancels_siblings_when_internal_task_fails(self) -> None:
        provider = FakeProvider("exa_mcp")
        service, result = self.retrieve([provider])
        first_publication = result.selected_publication_numbers[0]
        cancelled = []

        async def fail_one_and_wait(
            run_id, publication, urls, language, *, providers=None
        ):
            if publication == first_publication:
                await asyncio.sleep(0.01)
                raise RuntimeError("internal fetch failure")
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                cancelled.append(publication)
                raise

        service._fetch_with_fallback = fail_one_and_wait
        with self.assertRaisesRegex(RuntimeError, "internal fetch failure"):
            asyncio.run(
                service.fetch_selected(run_id=self.run["run_id"], retrieval=result)
            )
        self.assertTrue(cancelled)


if __name__ == "__main__":
    unittest.main()
