from __future__ import annotations

import unittest
from contextlib import contextmanager

from landscape.classification_terminal import (
    ClassificationAction,
    ClassificationResult,
    ClassificationTerminal,
)
from landscape.direction_record import DirectionRecord, DirectionStatus
from landscape.semantic_result_repository import (
    PostgreSQLClassificationRepository,
    PostgreSQLDirectionRepository,
    SemanticResultPersistenceError,
)


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
        target = self.connection.classification_values if "classification" in sql else self.connection.direction_values
        target.extend(
            {"value_type": row[2], "value_text": row[3], "sort_order": row[4]}
            for row in values
        )


class Connection:
    def __init__(self):
        self.run = {"taxonomy_version": "TAX-fixture"}
        self.direction_row = None
        self.direction_values = []
        self.classification_row = None
        self.classification_values = []

    def cursor(self):
        return Cursor(self)

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        if "FROM landscape_v4_runs" in normalized:
            return Cursor(self, self.run)
        if normalized.startswith("INSERT INTO landscape_v4_direction_records"):
            if self.direction_row is not None:
                return Cursor(self)
            keys = (
                "run_id",
                "analysis_unit_id",
                "taxonomy_version",
                "status",
                "evidence_sufficient",
                "technical_problem",
                "solution_mechanism",
                "technical_object",
                "direction_summary",
                "confidence",
                "unresolved_reason",
                "input_hash",
            )
            self.direction_row = dict(zip(keys, params, strict=True))
            return Cursor(self, {"analysis_unit_id": params[1]})
        if "FROM landscape_v4_direction_records" in normalized:
            return Cursor(self, self.direction_row)
        if "FROM landscape_v4_direction_values" in normalized:
            return Cursor(self, rows=self.direction_values)
        if normalized.startswith("INSERT INTO landscape_v4_classification_results"):
            if self.classification_row is not None:
                return Cursor(self)
            keys = (
                "run_id",
                "analysis_unit_id",
                "taxonomy_version",
                "action",
                "terminal",
                "primary_category_id",
                "confidence",
                "review_round",
                "unresolved_reason",
                "input_hash",
            )
            self.classification_row = dict(zip(keys, params, strict=True))
            return Cursor(self, {"analysis_unit_id": params[1]})
        if "FROM landscape_v4_classification_results" in normalized:
            return Cursor(self, self.classification_row)
        if "FROM landscape_v4_classification_values" in normalized:
            return Cursor(self, rows=self.classification_values)
        raise AssertionError(normalized)


def direction_value() -> DirectionRecord:
    return DirectionRecord(
        analysis_unit_id="AU-0000000000000001",
        status=DirectionStatus.AVAILABLE,
        evidence_sufficient=True,
        solution_mechanism="信道估计",
        direction_summary="无线信号处理",
        confidence=0.8,
        evidence_ids=("EV-1",),
        keywords=("信道",),
    )


def classification_value() -> ClassificationResult:
    return ClassificationResult(
        analysis_unit_id="AU-0000000000000001",
        action=ClassificationAction.EXACT_CATEGORY,
        terminal=ClassificationTerminal.CLASSIFIED,
        primary_category_id="C-LEAF-1",
        auxiliary_category_ids=("C-LEAF-2",),
        evidence_ids=("EV-1",),
        confidence=0.75,
        review_round=1,
    )


class LandscapeSemanticResultRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.connection = Connection()

        @contextmanager
        def connect():
            yield self.connection

        self.direction_repository = PostgreSQLDirectionRepository(
            "postgresql://fixture",
            connect=connect,
        )
        self.classification_repository = PostgreSQLClassificationRepository(
            "postgresql://fixture",
            connect=connect,
        )

    def test_direction_is_relational_idempotent_and_replayable(self):
        record = direction_value()
        self.assertEqual(self.direction_repository.put("run", record), record)
        self.assertEqual(self.direction_repository.put("run", record), record)
        self.assertEqual(
            self.direction_repository.get("run", record.analysis_unit_id),
            record,
        )
        self.assertEqual(len(self.connection.direction_values), 2)

    def test_different_direction_for_same_unit_fails_closed(self):
        self.direction_repository.put("run", direction_value())
        changed = direction_value().model_copy(update={"direction_summary": "different"})
        with self.assertRaisesRegex(SemanticResultPersistenceError, "immutable"):
            self.direction_repository.put("run", changed)

    def test_classification_is_relational_idempotent_and_replayable(self):
        result = classification_value()
        self.assertEqual(self.classification_repository.put("run", result), result)
        self.assertEqual(self.classification_repository.put("run", result), result)
        self.assertEqual(
            self.classification_repository.get("run", result.analysis_unit_id),
            result,
        )
        self.assertEqual(len(self.connection.classification_values), 2)

    def test_different_classification_for_same_unit_fails_closed(self):
        self.classification_repository.put("run", classification_value())
        changed = classification_value().model_copy(update={"confidence": 0.5})
        with self.assertRaisesRegex(SemanticResultPersistenceError, "immutable"):
            self.classification_repository.put("run", changed)


if __name__ == "__main__":
    unittest.main()
