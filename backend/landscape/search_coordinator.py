from __future__ import annotations

from idea.providers.base import PagedSearchProvider

from .publication_freeze import FrozenPublicationSet
from .search_execution import QuerySearchResult
from .scale_gate import ScaleEstimate, estimate_scale
from .scale_repository import ScaleDecision, ScaleGatePersistenceError


class SearchCoordinationError(RuntimeError):
    pass


class LandscapeSearchCoordinator:
    def __init__(
        self,
        *,
        query_repository,
        page_repository,
        scale_repository,
        publication_repository,
        execution,
    ):
        self.query_repository = query_repository
        self.page_repository = page_repository
        self.scale_repository = scale_repository
        self.publication_repository = publication_repository
        self.execution = execution

    async def estimate(
        self, run_id: str, provider: PagedSearchProvider
    ) -> ScaleEstimate:
        plan = self.query_repository.get(run_id)
        first_pages = await self.execution.estimate_plan(
            provider,
            run_id,
            plan.queries,
            self.page_repository,
        )
        totals = []
        for page in first_pages:
            if page.reported_total_results is None:
                if page.next_cursor is None:
                    totals.append(len(page.hits))
                else:
                    raise SearchCoordinationError(
                        "provider omitted total while more pages are available"
                    )
            else:
                totals.append(page.reported_total_results)
        estimate = estimate_scale(plan, tuple(totals), page_size=self.execution.page_size)
        return self.scale_repository.record(run_id, estimate).estimate

    async def complete(
        self, run_id: str, provider: PagedSearchProvider
    ) -> FrozenPublicationSet:
        results = await self.retrieve(run_id, provider)
        return self.freeze(run_id, results)

    async def retrieve(
        self, run_id: str, provider: PagedSearchProvider
    ) -> tuple[QuerySearchResult, ...]:
        gate = self.scale_repository.get(run_id)
        if gate.decision != ScaleDecision.APPROVED:
            raise ScaleGatePersistenceError(
                "search cannot continue before scale approval"
            )
        plan = self.query_repository.get(run_id)
        return await self.execution.execute_plan(
            provider,
            run_id,
            plan.queries,
            self.page_repository,
        )

    def freeze(
        self, run_id: str, results: tuple[QuerySearchResult, ...]
    ) -> FrozenPublicationSet:
        frozen = self.execution.freeze_results(run_id, results)
        return self.publication_repository.put(frozen)


__all__ = ["LandscapeSearchCoordinator", "SearchCoordinationError"]
