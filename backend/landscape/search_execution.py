from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from idea.providers.base import PagedSearchProvider, SearchPage, SearchQuery

from .query_planning import V4SearchQuery


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
    return SearchQuery(
        query_id=query.query_id,
        text=query.query_text,
        language="mixed" if any("\u3400" <= char <= "\u9fff" for term in query.terms for char in term) else "en",
        round_number=1,
        limit=limit,
        query_type="technical_means",
        material_types=["patent"],
    )


class PagedSearchExecutionService:
    def __init__(self, *, max_concurrency: int = 8, page_size: int = 100):
        if not 1 <= max_concurrency <= 64:
            raise ValueError("max_concurrency must be between 1 and 64")
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        self.max_concurrency = max_concurrency
        self.page_size = page_size

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
            cursor = persisted.next_cursor
            page_number += 1
        return QuerySearchResult(query.query_id, tuple(pages))

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
    def _validate_history(pages: tuple[SearchPage, ...]) -> None:
        for expected, page in enumerate(pages, start=1):
            if page.page_number != expected:
                raise SearchExecutionError("stored search page history is not contiguous")
            if expected < len(pages) and page.next_cursor is None:
                raise SearchExecutionError("stored search history continues after terminal page")


__all__ = ["PagedSearchExecutionService", "QuerySearchResult", "SearchExecutionError", "to_provider_query"]
