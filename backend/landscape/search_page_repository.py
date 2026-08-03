from __future__ import annotations

import json
import time
from contextlib import contextmanager
from typing import Iterator

from idea.providers.base import SearchPage


class SearchPagePersistenceError(RuntimeError):
    pass


class PostgreSQLSearchPageRepository:
    def __init__(self, dsn: str, *, connect=None):
        if not isinstance(dsn, str) or not dsn.strip():
            raise ValueError("PostgreSQL DSN must not be blank")
        self._dsn = dsn.strip()
        self._connect_factory = connect

    def put(self, run_id: str, query_id: str, cursor_in: str | None, page: SearchPage) -> SearchPage:
        page = SearchPage.model_validate(page.model_dump(mode="json"))
        with self._connect() as connection:
            query = connection.execute(
                "SELECT query_id FROM landscape_v4_search_queries WHERE run_id=%s AND query_id=%s",
                (run_id, query_id),
            ).fetchone()
            if query is None:
                raise KeyError((run_id, query_id))
            values = (
                run_id, query_id, page.page_number, cursor_in, page.next_cursor,
                page.reported_total_results, page.reported_total_pages,
                page.provider_request_id, page.stop_reason.value,
                json.dumps([hit.model_dump(mode="json") for hit in page.hits], ensure_ascii=False),
                int(time.time() * 1000),
            )
            inserted = connection.execute(
                """
                INSERT INTO landscape_v4_search_pages(
                    run_id,query_id,page_number,cursor_in,next_cursor,
                    reported_total_results,reported_total_pages,provider_request_id,
                    stop_reason,hits_json,created_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
                ON CONFLICT (run_id,query_id,page_number) DO NOTHING
                RETURNING page_number
                """,
                values,
            ).fetchone()
            if inserted is None:
                row = connection.execute(
                    "SELECT * FROM landscape_v4_search_pages WHERE run_id=%s AND query_id=%s AND page_number=%s",
                    (run_id, query_id, page.page_number),
                ).fetchone()
                if row is None or self._decode(row) != page or row["cursor_in"] != cursor_in:
                    raise SearchPagePersistenceError("search page checkpoint is immutable")
            return page

    def list(self, run_id: str, query_id: str) -> tuple[SearchPage, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM landscape_v4_search_pages WHERE run_id=%s AND query_id=%s ORDER BY page_number",
                (run_id, query_id),
            ).fetchall()
        pages = tuple(self._decode(row) for row in rows)
        for expected, page in enumerate(pages, start=1):
            if page.page_number != expected:
                raise SearchPagePersistenceError("search page checkpoints are not contiguous")
        return pages

    @staticmethod
    def _decode(row) -> SearchPage:
        try:
            raw_hits = row["hits_json"]
            payload = {
                "hits": json.loads(raw_hits) if isinstance(raw_hits, str) else raw_hits,
                "next_cursor": row["next_cursor"],
                "page_number": row["page_number"],
                "reported_total_results": row["reported_total_results"],
                "reported_total_pages": row["reported_total_pages"],
                "provider_request_id": row["provider_request_id"],
                "stop_reason": row["stop_reason"],
            }
            return SearchPage.model_validate(payload)
        except Exception as exc:
            raise SearchPagePersistenceError("stored search page failed validation") from exc

    @contextmanager
    def _connect(self) -> Iterator[object]:
        if self._connect_factory is not None:
            with self._connect_factory() as connection:
                yield connection
            return
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(self._dsn, row_factory=dict_row, connect_timeout=10) as connection:
            yield connection


__all__ = ["PostgreSQLSearchPageRepository", "SearchPagePersistenceError"]
