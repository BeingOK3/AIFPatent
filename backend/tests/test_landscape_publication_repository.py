from __future__ import annotations

import unittest
from contextlib import contextmanager

from idea.providers.base import SearchHit
from landscape.publication_freeze import freeze_publications
from landscape.publication_repository import PostgreSQLPublicationRepository, PublicationPersistenceError


class Cursor:
    def __init__(self, connection, row=None, rows=None):
        self.connection, self.row = connection, row
        self.rows = rows if rows is not None else ([] if row is None else [row])
    def fetchone(self): return self.row
    def fetchall(self): return self.rows
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def executemany(self, sql, values):
        normalized = " ".join(sql.split())
        if normalized.startswith("INSERT INTO landscape_v4_publications"):
            keys = (
                "run_id","publication_id","publication_identity","publication_number",
                "application_number","title","snippet","url","priority_date",
                "filing_date","publication_date","assignee","family_id","provider",
                "content_hash","sort_order",
            )
            self.connection.publications.extend(dict(zip(keys, row, strict=True)) for row in values)
        elif normalized.startswith("INSERT INTO landscape_v4_publication_sources"):
            keys = ("run_id","publication_id","query_id")
            self.connection.sources.extend(dict(zip(keys, row, strict=True)) for row in values)
        else: raise AssertionError(normalized)


class Connection:
    def __init__(self):
        self.run = True; self.manifest = None; self.publications = []; self.sources = []
    def cursor(self): return Cursor(self)
    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        if "FROM landscape_v4_runs" in normalized: return Cursor(self, {"run_id": params[0]} if self.run else None)
        if normalized.startswith("INSERT INTO landscape_v4_publication_sets"):
            if self.manifest is not None: return Cursor(self)
            keys = ("run_id","freeze_hash","publication_count","analysis_unit_count","created_at")
            self.manifest = dict(zip(keys, params, strict=True)); return Cursor(self, {"run_id": params[0]})
        if "FROM landscape_v4_publication_sets" in normalized: return Cursor(self, self.manifest)
        if "FROM landscape_v4_publications" in normalized:
            return Cursor(self, rows=sorted(self.publications, key=lambda row: row["sort_order"]))
        if "FROM landscape_v4_publication_sources" in normalized:
            return Cursor(self, rows=sorted(self.sources, key=lambda row: (row["publication_id"], row["query_id"])))
        raise AssertionError(normalized)


def frozen(number="US123A1"):
    hit = SearchHit(provider="fixture", provider_rank=1, publication_number=number, title="Title")
    return freeze_publications("LRN-0000000000000001", (("LQ4-0000000000000001", hit),))


class LandscapePublicationRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.connection = Connection()
        @contextmanager
        def connect(): yield self.connection
        self.repository = PostgreSQLPublicationRepository("postgresql://fixture", connect=connect)

    def test_frozen_set_is_relational_idempotent_and_replayable(self):
        value = frozen()
        self.assertEqual(self.repository.put(value), value)
        self.assertEqual(self.repository.put(value), value)
        self.assertEqual(self.repository.get(value.run_id), value)
        self.assertEqual(len(self.connection.publications), 1)
        self.assertEqual(len(self.connection.sources), 1)

    def test_different_set_for_same_run_fails_closed(self):
        self.repository.put(frozen())
        with self.assertRaisesRegex(PublicationPersistenceError, "different immutable"):
            self.repository.put(frozen("US999A1"))

    def test_unknown_run_fails_before_rows(self):
        self.connection.run = False
        with self.assertRaises(KeyError): self.repository.put(frozen())
        self.assertIsNone(self.connection.manifest)


if __name__ == "__main__": unittest.main()
