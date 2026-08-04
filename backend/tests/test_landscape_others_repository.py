from __future__ import annotations

import unittest
from contextlib import contextmanager

from landscape.others_discovery import (
    OthersClusterKind,
    OthersDiscovery,
)
from landscape.others_repository import PostgreSQLOthersRepository
from landscape.semantic_result_repository import SemanticResultPersistenceError


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

    def executemany(self, _sql, values):
        self.connection.members.extend(
            {
                "analysis_unit_id": row[2],
                "cluster_id": row[1],
                "sort_order": row[3],
            }
            for row in values
        )


class Connection:
    def __init__(self):
        self.run = {"taxonomy_version": "TAX-fixture"}
        self.manifest = None
        self.rows = {}
        self.members = []

    def cursor(self):
        return Cursor(self)

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        if "FROM landscape_v4_runs" in normalized:
            return Cursor(self, self.run)
        if normalized.startswith("INSERT INTO landscape_v4_others_manifests"):
            if self.manifest is None:
                keys = (
                    "run_id", "taxonomy_version", "algorithm_version", "member_count",
                    "cluster_count", "input_hash",
                )
                self.manifest = dict(zip(keys, params, strict=True))
            return Cursor(self)
        if "FROM landscape_v4_others_manifests" in normalized:
            return Cursor(self, self.manifest)
        if normalized.startswith("INSERT INTO landscape_v4_others_clusters"):
            key = params[1]
            if key in self.rows:
                return Cursor(self)
            keys = (
                "run_id", "cluster_id", "taxonomy_version", "algorithm_version", "kind",
                "representative_analysis_unit_id", "cohesion", "name", "technical_problem",
                "common_mechanism", "direction_boundary", "keywords_json", "naming_source",
                "input_hash",
            )
            self.rows[key] = dict(zip(keys, params, strict=True))
            return Cursor(self, {"cluster_id": key})
        if "FROM landscape_v4_others_clusters" in normalized:
            return Cursor(self, rows=list(self.rows.values()))
        if "FROM landscape_v4_others_members" in normalized:
            cluster_id = params[1]
            return Cursor(self, rows=[row for row in self.members if row["cluster_id"] == cluster_id])
        raise AssertionError(normalized)


def discovery() -> OthersDiscovery:
    return OthersDiscovery(
        source_member_ids=("AU-0000000000000001",),
        clusters=(
            {
                "cluster_id": "OC-1111111111111111",
                "kind": OthersClusterKind.NOISE,
                "member_ids": ("AU-0000000000000001",),
                "representative_analysis_unit_id": "AU-0000000000000001",
                "cohesion": 1,
                "name": "确定性 Others",
            },
        ),
    )


class LandscapeOthersRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.connection = Connection()

        @contextmanager
        def connect():
            yield self.connection

        self.repository = PostgreSQLOthersRepository("postgresql://fixture", connect=connect)

    def test_others_partition_is_idempotent_and_replayable(self):
        value = discovery()
        self.assertEqual(self.repository.put("run", value), value)
        self.assertEqual(self.repository.put("run", value), value)
        self.assertEqual(self.repository.get("run"), value)
        self.assertEqual(len(self.connection.members), 1)

    def test_changed_cluster_fails_closed(self):
        value = discovery()
        self.repository.put("run", value)
        changed = value.model_copy(update={"clusters": (value.clusters[0].model_copy(update={"name": "changed"}),)})
        with self.assertRaisesRegex(SemanticResultPersistenceError, "immutable"):
            self.repository.put("run", changed)

    def test_empty_partition_has_explicit_completion_manifest(self):
        empty = OthersDiscovery(source_member_ids=(), clusters=())
        self.assertEqual(self.repository.put("run", empty), empty)
        self.assertEqual(self.repository.get("run"), empty)
        self.assertEqual(self.connection.manifest["member_count"], 0)
        self.assertEqual(self.connection.manifest["cluster_count"], 0)


if __name__ == "__main__":
    unittest.main()
