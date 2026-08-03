from __future__ import annotations

import unittest
from contextlib import contextmanager

from idea.providers.base import PageStopReason, SearchHit, SearchPage
from landscape.search_page_repository import PostgreSQLSearchPageRepository, SearchPagePersistenceError


class Cursor:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows if rows is not None else ([] if row is None else [row])
    def fetchone(self): return self.row
    def fetchall(self): return self.rows


class Connection:
    def __init__(self): self.rows = {}; self.query_exists = True
    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        if "FROM landscape_v4_search_queries" in normalized:
            return Cursor({"query_id": params[1]}) if self.query_exists else Cursor()
        if normalized.startswith("INSERT INTO landscape_v4_search_pages"):
            key = params[:3]
            if key in self.rows: return Cursor()
            keys = ("run_id","query_id","page_number","cursor_in","next_cursor","reported_total_results","reported_total_pages","provider_request_id","stop_reason","hits_json","created_at")
            self.rows[key] = dict(zip(keys, params, strict=True))
            return Cursor({"page_number": params[2]})
        if "page_number=%s" in normalized:
            return Cursor(self.rows.get(params))
        if "ORDER BY page_number" in normalized:
            rows = [row for key, row in self.rows.items() if key[:2] == params]
            return Cursor(rows=sorted(rows, key=lambda row: row["page_number"]))
        raise AssertionError(normalized)


def page(number=1, next_cursor="page:2"):
    return SearchPage(
        hits=[SearchHit(provider="serpapi", provider_rank=1, publication_number="US123A1")],
        next_cursor=next_cursor,
        page_number=number,
        reported_total_results=250,
        reported_total_pages=3,
        provider_request_id=f"request-{number}",
        stop_reason=PageStopReason.MORE_AVAILABLE if next_cursor else PageStopReason.QUERY_EXHAUSTED,
    )


class LandscapeSearchPageRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.connection = Connection()
        @contextmanager
        def connect(): yield self.connection
        self.repository = PostgreSQLSearchPageRepository("postgresql://fixture", connect=connect)

    def test_page_checkpoint_is_idempotent_and_replayable(self):
        first = page()
        self.assertEqual(self.repository.put("run", "query", None, first), first)
        self.assertEqual(self.repository.put("run", "query", None, first), first)
        self.assertEqual(self.repository.list("run", "query"), (first,))

    def test_same_page_with_different_cursor_or_content_fails_closed(self):
        self.repository.put("run", "query", None, page())
        with self.assertRaisesRegex(SearchPagePersistenceError, "immutable"):
            self.repository.put("run", "query", "wrong", page())

    def test_non_contiguous_resume_history_fails_closed(self):
        self.repository.put("run", "query", "page:2", page(2, None))
        with self.assertRaisesRegex(SearchPagePersistenceError, "contiguous"):
            self.repository.list("run", "query")


if __name__ == "__main__": unittest.main()
