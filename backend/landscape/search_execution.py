from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from idea.providers.base import PageStopReason, PagedSearchProvider, SearchPage, SearchQuery

from .query_planning import V4SearchQuery
from .publication_freeze import FrozenPublicationSet, freeze_publications


class SearchExecutionError(RuntimeError):
    pass


class SearchPageCheckpoint(Protocol):
    def put(self, run_id: str, query_id: str, cursor_in: str | None, page: SearchPage) -> SearchPage: ...
    def list(self, run_id: str, query_id: str) -> tuple[SearchPage, ...]: ...


@dataclass(frozen=True)
class QuerySearchResult:
    query_id: str
    pages: tuple[SearchPage, ...]

    @property
    def hits(self):
        return tuple(hit for page in self.pages for hit in page.hits)


def to_provider_query(query: V4SearchQuery, *, limit: int = 100) -> SearchQuery:
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    # Google Patents treats ``after`` and ``before`` as strict bounds. Move
    # both ends out by one day so the user-selected interval remains
    # inclusive. The freeze stage still applies an authoritative local gate.
    after = (query.publication_start - timedelta(days=1)).strftime("%Y%m%d")
    before = (query.publication_end + timedelta(days=1)).strftime("%Y%m%d")
    return SearchQuery(
        query_id=query.query_id,
        text=(
            f"{query.query_text} after=publication:{after} "
            f"before=publication:{before}"
        ),
        language="mixed" if any("\u3400" <= char <= "\u9fff" for term in query.terms for char in term) else "en",
        round_number=1,
        limit=limit,
        query_type="technical_means",
        material_types=["patent"],
    )


class PagedSearchExecutionService:
    def __init__(
        self,
        *,
        max_concurrency: int = 8,
        page_size: int = 100,
        max_pages_per_query: int = 8,
        max_frozen_publications: int = 3000,
    ):
        if not 1 <= max_concurrency <= 64:
            raise ValueError("max_concurrency must be between 1 and 64")
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        if not 1 <= max_pages_per_query <= 100:
            raise ValueError("max_pages_per_query must be between 1 and 100")
        if max_frozen_publications < 1:
            raise ValueError("max_frozen_publications must be at least 1")
        self.max_concurrency = max_concurrency
        self.page_size = page_size
        self.max_pages_per_query = max_pages_per_query
        self.max_frozen_publications = max_frozen_publications

    async def execute_query(
        self,
        provider: PagedSearchProvider,
        run_id: str,
        query: V4SearchQuery,
        checkpoints: SearchPageCheckpoint,
    ) -> QuerySearchResult:
        existing = checkpoints.list(run_id, query.query_id)
        if existing:
            self._validate_history(existing)
            pages = list(existing)
            if pages[-1].next_cursor is None:
                return QuerySearchResult(query.query_id, tuple(pages))
            cursor = pages[-1].next_cursor
            page_number = pages[-1].page_number + 1
        else:
            pages = []
            cursor = None
            page_number = 1
        provider_query = to_provider_query(query, limit=self.page_size)
        while True:
            page = await provider.search_page(provider_query, cursor)
            if page.page_number != page_number:
                raise SearchExecutionError(
                    f"provider returned page {page.page_number}, expected {page_number}"
                )
            persisted = checkpoints.put(run_id, query.query_id, cursor, page)
            pages.append(persisted)
            if persisted.next_cursor is None:
                break
            if len(pages) >= self.max_pages_per_query:
                capped = page.model_copy(
                    update={
                        "stop_reason": PageStopReason.MAX_PAGES,
                        "next_cursor": None,
                        "hits": [],
                        "page_number": persisted.page_number + 1,
                    }
                )
                checkpoints.put(
                    run_id, query.query_id, persisted.next_cursor, capped
                )
                pages.append(capped)
                break
            cursor = persisted.next_cursor
            page_number += 1
        return QuerySearchResult(query.query_id, tuple(pages))

    async def checkpoint_first_page(
        self,
        provider: PagedSearchProvider,
        run_id: str,
        query: V4SearchQuery,
        checkpoints: SearchPageCheckpoint,
    ) -> SearchPage:
        existing = checkpoints.list(run_id, query.query_id)
        if existing:
            self._validate_history(existing)
            return existing[0]
        page = await provider.search_page(
            to_provider_query(query, limit=self.page_size),
            None,
        )
        if page.page_number != 1:
            raise SearchExecutionError(
                f"provider returned page {page.page_number}, expected 1"
            )
        return checkpoints.put(run_id, query.query_id, None, page)

    async def estimate_plan(
        self,
        provider: PagedSearchProvider,
        run_id: str,
        queries: tuple[V4SearchQuery, ...],
        checkpoints: SearchPageCheckpoint,
    ) -> tuple[SearchPage, ...]:
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def one(query: V4SearchQuery) -> SearchPage:
            async with semaphore:
                return await self.checkpoint_first_page(
                    provider, run_id, query, checkpoints
                )

        return tuple(await asyncio.gather(*(one(query) for query in queries)))

    async def execute_plan(
        self,
        provider: PagedSearchProvider,
        run_id: str,
        queries: tuple[V4SearchQuery, ...],
        checkpoints: SearchPageCheckpoint,
    ) -> tuple[QuerySearchResult, ...]:
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def one(query: V4SearchQuery) -> QuerySearchResult:
            async with semaphore:
                return await self.execute_query(provider, run_id, query, checkpoints)

        # gather preserves deterministic plan order while allowing bounded
        # concurrency between independent query streams.
        return tuple(await asyncio.gather(*(one(query) for query in queries)))

    @staticmethod
    def freeze_results(
        run_id: str,
        results: tuple[QuerySearchResult, ...],
        *,
        publication_start=None,
        publication_end=None,
        max_publications: int | None = None,
    ) -> FrozenPublicationSet:
        return freeze_publications(
            run_id,
            tuple(
                (result.query_id, hit)
                for result in results
                for hit in result.hits
            ),
            publication_start=publication_start,
            publication_end=publication_end,
            max_publications=max_publications,
        )

    @staticmethod
    def _validate_history(pages: tuple[SearchPage, ...]) -> None:
        for expected, page in enumerate(pages, start=1):
            if page.page_number != expected:
                raise SearchExecutionError("stored search page history is not contiguous")
            if expected < len(pages) and page.next_cursor is None:
                raise SearchExecutionError("stored search history continues after terminal page")


__all__ = ["PagedSearchExecutionService", "QuerySearchResult", "SearchExecutionError", "to_provider_query"]
