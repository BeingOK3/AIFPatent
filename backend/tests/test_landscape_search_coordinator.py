from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from idea.providers.base import PageStopReason
from landscape.query_planning import build_query_plan
from landscape.scale_repository import ScaleDecision, ScaleGatePersistenceError
from landscape.search_coordinator import LandscapeSearchCoordinator, SearchCoordinationError
from landscape.search_execution import PagedSearchExecutionService
from tests.test_landscape_query_planning import confirmed_scope
from tests.test_landscape_search_execution import Checkpoints, Provider


class QueryRepository:
    def __init__(self, plan): self.plan = plan
    def get(self, _run_id): return self.plan


class ScaleRepository:
    def __init__(self): self.record_value = None
    def record(self, run_id, estimate):
        self.record_value = SimpleNamespace(run_id=run_id, estimate=estimate, decision=ScaleDecision.APPROVED)
        return self.record_value
    def get(self, _run_id): return self.record_value


class PublicationRepository:
    def __init__(self): self.value = None
    def put(self, value): self.value = value; return value


def coordinator(plan, scale=None):
    scale = scale or ScaleRepository()
    publications = PublicationRepository()
    value = LandscapeSearchCoordinator(
        query_repository=QueryRepository(plan),
        page_repository=Checkpoints(),
        scale_repository=scale,
        publication_repository=publications,
        execution=PagedSearchExecutionService(max_concurrency=2),
    )
    return value, scale, publications


class LandscapeSearchCoordinatorTests(unittest.TestCase):
    def test_estimate_stops_after_first_page_then_approved_complete_resumes(self):
        plan = build_query_plan(confirmed_scope(companies=(("华为", ("华为",)),)))
        service, scale, publications = coordinator(plan)
        provider = Provider()
        async def scenario():
            estimate = await service.estimate("run", provider)
            self.assertEqual(len(provider.calls), 1)
            frozen = await service.complete("run", provider)
            return estimate, frozen
        estimate, frozen = asyncio.run(scenario())
        self.assertEqual(estimate.estimated_total_results, 2)
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(frozen, publications.value)
        self.assertEqual(frozen.publication_count, 2)

    def test_complete_is_blocked_without_scale_approval(self):
        plan = build_query_plan(confirmed_scope(companies=(("华为", ("华为",)),)))
        scale = ScaleRepository()
        scale.record_value = SimpleNamespace(decision=None)
        service, _, _ = coordinator(plan, scale)
        with self.assertRaisesRegex(ScaleGatePersistenceError, "approval"):
            asyncio.run(service.complete("run", Provider()))

    def test_missing_provider_total_with_more_pages_fails_closed(self):
        plan = build_query_plan(confirmed_scope(companies=(("华为", ("华为",)),)))
        service, _, _ = coordinator(plan)
        class MissingTotalProvider(Provider):
            async def search_page(self, query, cursor):
                page = await super().search_page(query, cursor)
                return page.model_copy(update={"reported_total_results": None})
        with self.assertRaisesRegex(SearchCoordinationError, "omitted total"):
            asyncio.run(service.estimate("run", MissingTotalProvider()))


if __name__ == "__main__": unittest.main()
