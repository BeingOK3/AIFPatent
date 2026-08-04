from __future__ import annotations

import asyncio
import unittest

from idea.providers.base import PageStopReason, SearchHit, SearchPage
from landscape.search_execution import PagedSearchExecutionService, SearchExecutionError, to_provider_query
from tests.test_landscape_query_planning import confirmed_scope
from landscape.query_planning import build_query_plan


class Provider:
    name = "fixture"
    def __init__(self):
        self.calls = []
    async def search_page(self, query, cursor):
        self.calls.append((query.query_id, cursor))
        number = 1 if cursor is None else int(cursor.split(":")[1])
        next_cursor = f"page:{number + 1}" if number < 2 else None
        return SearchPage(
            hits=[SearchHit(provider=self.name, provider_rank=number, publication_number=f"US{number}A1")],
            next_cursor=next_cursor,
            page_number=number,
            reported_total_results=2,
            reported_total_pages=2,
            provider_request_id=f"req-{number}",
            stop_reason=PageStopReason.MORE_AVAILABLE if next_cursor else PageStopReason.QUERY_EXHAUSTED,
        )


class Checkpoints:
    def __init__(self): self.data = {}
    def list(self, run_id, query_id): return tuple(self.data.get((run_id, query_id), ()))
    def put(self, run_id, query_id, cursor_in, page):
        key = (run_id, query_id)
        rows = self.data.setdefault(key, [])
        if rows and rows[-1].page_number >= page.page_number:
            return rows[page.page_number - 1]
        rows.append(page)
        return page


class LandscapeSearchExecutionTests(unittest.TestCase):
    def test_provider_query_contains_inclusive_publication_window(self):
        query = build_query_plan(
            confirmed_scope(companies=(("华为", ("华为",)),))
        ).queries[0]
        provider_query = to_provider_query(query)
        self.assertIn("after=publication:19891231", provider_query.text)
        self.assertIn("before=publication:20270101", provider_query.text)

    def test_query_stops_at_max_pages_and_records_cap_stop_reason(self):
        plan = build_query_plan(confirmed_scope(companies=(("华为", ("华为",)),)))
        provider, checkpoints = Provider(), Checkpoints()
        service = PagedSearchExecutionService(max_concurrency=1, max_pages_per_query=1)
        result = asyncio.run(
            service.execute_query(provider, "run", plan.queries[0], checkpoints)
        )
        self.assertEqual(len(result.pages), 2)
        self.assertEqual(result.pages[-1].stop_reason, PageStopReason.MAX_PAGES)
        self.assertIsNone(result.pages[-1].next_cursor)
        self.assertEqual(result.pages[-1].hits, [])
        second = asyncio.run(
            service.execute_query(provider, "run", plan.queries[0], checkpoints)
        )
        self.assertEqual(second, result)
        self.assertEqual(len(provider.calls), 1)

    def test_pages_are_sequential_and_resume_from_checkpoint(self):
        plan = build_query_plan(confirmed_scope(companies=(("华为", ("华为",)),)))
        provider, checkpoints = Provider(), Checkpoints()
        service = PagedSearchExecutionService(max_concurrency=2)
        async def scenario():
            first = await service.execute_query(provider, "run", plan.queries[0], checkpoints)
            second = await service.execute_query(provider, "run", plan.queries[0], checkpoints)
            return first, second
        first, second = asyncio.run(scenario())
        self.assertEqual(len(first.pages), 2)
        self.assertEqual(first, second)
        self.assertEqual(provider.calls, [(plan.queries[0].query_id, None), (plan.queries[0].query_id, "page:2")])

    def test_plan_runs_independently_in_bounded_parallelism(self):
        plan = build_query_plan(confirmed_scope(companies=(("华为", ("华为", "Huawei")),)))
        provider, checkpoints = Provider(), Checkpoints()
        service = PagedSearchExecutionService(max_concurrency=1)
        async def scenario():
            return await service.execute_plan(provider, "run", plan.queries, checkpoints)
        results = asyncio.run(scenario())
        self.assertEqual([result.query_id for result in results], [query.query_id for query in plan.queries])
        self.assertEqual(len(provider.calls), 4)

    def test_provider_page_number_mismatch_fails_closed(self):
        plan = build_query_plan(confirmed_scope(companies=(("华为", ("华为",)),)))
        class BadProvider(Provider):
            async def search_page(self, query, cursor):
                page = await super().search_page(query, cursor)
                return page.model_copy(update={"page_number": page.page_number + 1})
        async def scenario():
            await PagedSearchExecutionService().execute_query(BadProvider(), "run", plan.queries[0], Checkpoints())
        with self.assertRaises(SearchExecutionError): asyncio.run(scenario())

    def test_completed_pages_can_be_frozen_into_publications(self):
        plan = build_query_plan(confirmed_scope(companies=(("华为", ("华为",)),)))
        provider, checkpoints = Provider(), Checkpoints()
        service = PagedSearchExecutionService()
        async def scenario():
            return await service.execute_plan(provider, "run", plan.queries, checkpoints)
        result = service.freeze_results("run", asyncio.run(scenario()))
        self.assertEqual(result.publication_count, 2)

    def test_estimation_fetches_only_first_page_and_is_resumable(self):
        plan = build_query_plan(confirmed_scope(companies=(("华为", ("华为",)),)))
        provider, checkpoints = Provider(), Checkpoints()
        service = PagedSearchExecutionService()
        async def scenario():
            first = await service.estimate_plan(provider, "run", plan.queries, checkpoints)
            second = await service.estimate_plan(provider, "run", plan.queries, checkpoints)
            return first, second
        first, second = asyncio.run(scenario())
        self.assertEqual(first, second)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(provider.calls[0][1], None)


if __name__ == "__main__": unittest.main()
