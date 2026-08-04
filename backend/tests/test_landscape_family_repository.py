from __future__ import annotations

import unittest
from contextlib import contextmanager

from idea.providers.base import SearchHit
from landscape.family_repository import FamilyPersistenceError, PostgreSQLFamilyRepository
from landscape.family_resolution import resolve_frozen_publications
from landscape.publication_freeze import freeze_publications


class Cursor:
    def __init__(self, connection, row=None, rows=None):
        self.connection = connection
        self.row = row
        self.rows = rows or ([] if row is None else [row])

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def executemany(self, sql, values):
        normalized = " ".join(sql.split())
        if "landscape_v4_analysis_units" in normalized:
            keys = ("run_id", "analysis_unit_id", "merge_basis", "sort_order")
            self.connection.units.extend(dict(zip(keys, row, strict=True)) for row in values)
        elif "landscape_v4_analysis_unit_members" in normalized:
            keys = ("run_id", "analysis_unit_id", "publication_id", "sort_order")
            self.connection.members.extend(dict(zip(keys, row, strict=True)) for row in values)
        else:
            raise AssertionError(normalized)


class Connection:
    def __init__(self, publication_ids):
        self.publication_ids = tuple(publication_ids)
        self.manifest = None
        self.units = []
        self.members = []

    def cursor(self):
        return Cursor(self)

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        if "FROM landscape_v4_publications" in normalized:
            return Cursor(self, rows=[{"publication_id": value} for value in sorted(self.publication_ids)])
        if normalized.startswith("INSERT INTO landscape_v4_family_manifests"):
            if self.manifest is not None:
                return Cursor(self)
            keys = (
                "run_id", "algorithm_version", "publication_count",
                "analysis_unit_count", "resolution_hash",
            )
            self.manifest = dict(zip(keys, params, strict=True))
            return Cursor(self, {"run_id": params[0]})
        if "FROM landscape_v4_family_manifests" in normalized:
            return Cursor(self, self.manifest)
        if "FROM landscape_v4_analysis_units" in normalized:
            return Cursor(self, rows=sorted(self.units, key=lambda row: row["sort_order"]))
        if "FROM landscape_v4_analysis_unit_members" in normalized:
            rows = [row for row in self.members if row["analysis_unit_id"] == params[1]]
            return Cursor(self, rows=sorted(rows, key=lambda row: row["sort_order"]))
        raise AssertionError(normalized)


def fixture():
    frozen = freeze_publications(
        "run",
        (
            (
                "q",
                SearchHit(
                    provider="fixture",
                    provider_rank=1,
                    publication_number="US1A1",
                    application_number="APP-1",
                ),
            ),
            (
                "q",
                SearchHit(
                    provider="fixture",
                    provider_rank=2,
                    publication_number="US1B2",
                    application_number="APP-1",
                ),
            ),
        ),
    )
    return frozen, resolve_frozen_publications(frozen)


class LandscapeFamilyRepositoryTests(unittest.TestCase):
    def setUp(self):
        frozen, self.resolution = fixture()
        self.connection = Connection(item.publication_id for item in frozen.publications)

        @contextmanager
        def connect():
            yield self.connection

        self.repository = PostgreSQLFamilyRepository("postgresql://fixture", connect=connect)

    def test_family_partition_is_relational_idempotent_and_replayable(self):
        self.assertEqual(self.repository.put("run", self.resolution), self.resolution)
        self.assertEqual(self.repository.put("run", self.resolution), self.resolution)
        self.assertEqual(self.repository.get("run"), self.resolution)
        self.assertEqual(len(self.connection.units), 1)
        self.assertEqual(len(self.connection.members), 2)

    def test_missing_or_extra_publication_fails_before_write(self):
        self.connection.publication_ids = (*self.connection.publication_ids, "PUB-extra")
        with self.assertRaisesRegex(FamilyPersistenceError, "exactly partition"):
            self.repository.put("run", self.resolution)
        self.assertIsNone(self.connection.manifest)

    def test_changed_partition_for_same_run_fails_closed(self):
        self.repository.put("run", self.resolution)
        changed = self.resolution.model_copy(update={"resolution_hash": "0" * 64})
        with self.assertRaises(ValueError):
            self.repository.put("run", changed)


if __name__ == "__main__":
    unittest.main()
