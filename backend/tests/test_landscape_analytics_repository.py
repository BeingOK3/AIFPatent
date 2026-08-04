from __future__ import annotations

import unittest
from contextlib import contextmanager
from datetime import date

from landscape.analytics_repository import (
    PostgreSQLMetricRepository,
    PostgreSQLRepresentativeRepository,
    PostgreSQLTrendRepository,
)
from landscape.metrics import MetricAnalysisUnit, MetricPublication, build_metric_cube
from landscape.semantic_result_repository import SemanticResultPersistenceError
from landscape.representatives import select_representative_patents
from landscape.trends import build_trend_candidates


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
        if "landscape_v4_metric_buckets" in normalized:
            keys = ("run_id", "bucket_id", "sort_order", "label", "bucket_start", "bucket_end", "granularity")
            self.connection.buckets.extend(dict(zip(keys, row, strict=True)) for row in values)
        elif "landscape_v4_metric_cells" in normalized:
            keys = ("run_id", "direction_id", "organization_id", "bucket_id", "analysis_unit_count", "publication_count", "direction_share")
            self.connection.cells.extend(dict(zip(keys, row, strict=True)) for row in values)
        elif "landscape_v4_metric_cell_values" in normalized:
            keys = ("run_id", "direction_id", "organization_id", "bucket_id", "value_type", "value_id", "sort_order")
            self.connection.values.extend(dict(zip(keys, row, strict=True)) for row in values)
        elif "landscape_v4_trend_candidates" in normalized:
            keys = ("run_id", "candidate_id", "direction_id", "change_type", "conclusion_strength")
            self.connection.trend_candidates.extend(dict(zip(keys, row, strict=True)) for row in values)
        elif "landscape_v4_trend_bucket_metrics" in normalized:
            keys = ("run_id", "candidate_id", "bucket_id", "sort_order", "analysis_unit_count", "publication_count")
            self.connection.trend_buckets.extend(dict(zip(keys, row, strict=True)) for row in values)
        elif "landscape_v4_trend_values" in normalized:
            keys = ("run_id", "candidate_id", "value_type", "value_id", "sort_order")
            self.connection.trend_values.extend(dict(zip(keys, row, strict=True)) for row in values)
        elif "landscape_v4_representatives" in normalized:
            keys = (
                "run_id", "representative_id", "selection_rank", "direction_id",
                "analysis_unit_id", "publication_id", "title", "publication_number",
                "publication_date", "organization_ids_json", "classification_path_json",
                "selection_reasons_json", "patent_url", "link_status",
            )
            self.connection.representatives.extend(dict(zip(keys, row, strict=True)) for row in values)
        else:
            raise AssertionError(normalized)


class Connection:
    def __init__(self):
        self.manifest = None
        self.buckets = []
        self.cells = []
        self.values = []
        self.trend_manifest = None
        self.trend_candidates = []
        self.trend_buckets = []
        self.trend_values = []
        self.representative_manifest = None
        self.representatives = []

    def cursor(self):
        return Cursor(self)

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        if normalized.startswith("INSERT INTO landscape_v4_metric_manifests"):
            if self.manifest is not None:
                return Cursor(self)
            keys = (
                "run_id", "policy_version", "publication_start", "publication_end",
                "organization_counting_mode", "analysis_unit_time_policy",
                "analysis_unit_count", "publication_count", "excluded_publication_count",
                "cube_hash",
            )
            self.manifest = dict(zip(keys, params, strict=True))
            return Cursor(self, {"run_id": params[0]})
        if "FROM landscape_v4_metric_manifests" in normalized:
            return Cursor(self, self.manifest)
        if "FROM landscape_v4_metric_buckets" in normalized:
            return Cursor(self, rows=sorted(self.buckets, key=lambda row: row["sort_order"]))
        if "FROM landscape_v4_metric_cells" in normalized:
            return Cursor(self, rows=sorted(self.cells, key=lambda row: (row["direction_id"], row["organization_id"], row["bucket_id"])))
        if "FROM landscape_v4_metric_cell_values" in normalized:
            direction_id, organization_id, bucket_id = params[1:]
            rows = [
                row for row in self.values
                if row["direction_id"] == direction_id
                and row["organization_id"] == organization_id
                and row["bucket_id"] == bucket_id
            ]
            return Cursor(self, rows=sorted(rows, key=lambda row: (row["value_type"], row["sort_order"])))
        if normalized.startswith("INSERT INTO landscape_v4_trend_manifests"):
            if self.trend_manifest is not None:
                return Cursor(self)
            keys = ("run_id", "policy_version", "candidate_count", "input_hash")
            self.trend_manifest = dict(zip(keys, params, strict=True))
            return Cursor(self, {"run_id": params[0]})
        if "FROM landscape_v4_trend_manifests" in normalized:
            return Cursor(self, self.trend_manifest)
        if "FROM landscape_v4_trend_candidates" in normalized:
            return Cursor(self, rows=sorted(self.trend_candidates, key=lambda row: row["candidate_id"]))
        if "FROM landscape_v4_trend_bucket_metrics" in normalized:
            rows = [row for row in self.trend_buckets if row["candidate_id"] == params[1]]
            return Cursor(self, rows=sorted(rows, key=lambda row: row["sort_order"]))
        if "FROM landscape_v4_trend_values" in normalized:
            rows = [row for row in self.trend_values if row["candidate_id"] == params[1]]
            return Cursor(self, rows=sorted(rows, key=lambda row: (row["value_type"], row["sort_order"])))
        if normalized.startswith("INSERT INTO landscape_v4_representative_manifests"):
            if self.representative_manifest is not None:
                return Cursor(self)
            keys = ("run_id", "representative_count", "input_hash")
            self.representative_manifest = dict(zip(keys, params, strict=True))
            return Cursor(self, {"run_id": params[0]})
        if "FROM landscape_v4_representative_manifests" in normalized:
            return Cursor(self, self.representative_manifest)
        if "FROM landscape_v4_representatives" in normalized:
            return Cursor(self, rows=sorted(self.representatives, key=lambda row: (row["direction_id"], row["selection_rank"])))
        raise AssertionError(normalized)


def units():
    return (
        MetricAnalysisUnit(
            analysis_unit_id="AU-0000000000000001",
            direction_id="DIR-A",
            publications=(
                MetricPublication(
                    publication_id="PUB-1",
                    publication_number="CN1A",
                    publication_date=date(2024, 1, 1),
                    primary_organization_id="ORG-A",
                ),
            ),
            classification_path=("一级", "二级"),
            classification_confidence=0.8,
            evidence_completeness=1,
            evidence_ids=("EV-1",),
        ),
    )


def cube():
    return build_metric_cube(
        units(),
        publication_start=date(2024, 1, 1),
        publication_end=date(2024, 2, 1),
    )


class LandscapeMetricRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.connection = Connection()

        @contextmanager
        def connect():
            yield self.connection

        self.repository = PostgreSQLMetricRepository("postgresql://fixture", connect=connect)
        self.trend_repository = PostgreSQLTrendRepository("postgresql://fixture", connect=connect)
        self.representative_repository = PostgreSQLRepresentativeRepository(
            "postgresql://fixture", connect=connect
        )

    def test_metric_cube_is_relational_idempotent_and_replayable(self):
        value = cube()
        self.assertEqual(self.repository.put("run", value), value)
        self.assertEqual(self.repository.put("run", value), value)
        self.assertEqual(self.repository.get("run"), value)
        self.assertEqual(len(self.connection.cells), len(value.cells))
        self.assertEqual(len(self.connection.values), 2)

    def test_changed_cube_for_same_run_fails_closed(self):
        value = cube()
        self.repository.put("run", value)
        changed = value.model_copy(update={"cube_hash": "0" * 64})
        with self.assertRaises(ValueError):
            self.repository.put("run", changed)
        changed_manifest = dict(self.connection.manifest)
        changed_manifest["analysis_unit_count"] = 99
        self.connection.manifest = changed_manifest
        with self.assertRaises((SemanticResultPersistenceError, ValueError)):
            self.repository.get("run")

    def test_zero_and_nonzero_trends_have_immutable_manifests(self):
        metric_cube = cube()
        self.repository.put("run", metric_cube)
        candidates = build_trend_candidates(metric_cube, units())
        self.assertEqual(self.trend_repository.put("run", candidates), candidates)
        self.assertEqual(self.trend_repository.put("run", candidates), candidates)
        self.assertEqual(self.trend_repository.get("run"), candidates)

        empty_connection = Connection()

        @contextmanager
        def empty_connect():
            yield empty_connection

        empty_repository = PostgreSQLTrendRepository(
            "postgresql://fixture", connect=empty_connect
        )
        self.assertEqual(empty_repository.put("empty-run", ()), ())
        self.assertEqual(empty_repository.get("empty-run"), ())

    def test_representatives_are_ranked_idempotent_and_replayable(self):
        self.repository.put("run", cube())
        representatives = select_representative_patents(units(), direction_id="DIR-A")
        self.assertEqual(
            self.representative_repository.put("run", representatives),
            representatives,
        )
        self.assertEqual(
            self.representative_repository.put("run", representatives),
            representatives,
        )
        self.assertEqual(self.representative_repository.get("run"), representatives)

    def test_empty_representatives_have_completion_manifest(self):
        self.repository.put("run", cube())
        self.assertEqual(self.representative_repository.put("run", ()), ())
        self.assertEqual(self.representative_repository.get("run"), ())


if __name__ == "__main__":
    unittest.main()
