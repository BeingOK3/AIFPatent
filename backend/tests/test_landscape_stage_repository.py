from __future__ import annotations

import unittest
from contextlib import contextmanager

from landscape.stage_repository import (
    PostgreSQLStageRepository,
    STAGE_ORDER,
    StagePersistenceError,
    V4StageName,
    V4StageStatus,
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
        if "landscape_v4_run_stages" not in sql:
            raise AssertionError(sql)
        for run_id, stage_name, stage_order, updated_at in values:
            self.connection.stages.setdefault(
                stage_name,
                {
                    "run_id": run_id,
                    "stage_name": stage_name,
                    "stage_order": stage_order,
                    "status": "PENDING",
                    "attempt": 0,
                    "completed_count": 0,
                    "total_count": None,
                    "error_code": None,
                    "error_message": None,
                    "started_at": None,
                    "completed_at": None,
                    "updated_at": updated_at,
                },
            )


class Connection:
    def __init__(self):
        self.stages = {}
        self.limitations = {}

    def cursor(self):
        return Cursor(self)

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT run_id FROM landscape_v4_runs"):
            return Cursor(self, {"run_id": params[0]})
        if "FROM landscape_v4_run_stages" in normalized:
            if "stage_name=%s" in normalized:
                return Cursor(self, self.stages.get(params[1]))
            rows = sorted(self.stages.values(), key=lambda row: row["stage_order"])
            return Cursor(self, rows=rows)
        if normalized.startswith("UPDATE landscape_v4_run_stages SET status='RUNNING'"):
            total_count, started_at, updated_at, _run_id, stage_name = params
            row = self.stages[stage_name]
            row.update(
                status="RUNNING",
                attempt=row["attempt"] + 1,
                total_count=total_count,
                error_code=None,
                error_message=None,
                started_at=row["started_at"] or started_at,
                completed_at=None,
                updated_at=updated_at,
            )
            return Cursor(self)
        if normalized.startswith("UPDATE landscape_v4_run_stages SET completed_count="):
            completed, total, updated_at, _run_id, stage_name = params
            self.stages[stage_name].update(
                completed_count=completed, total_count=total, updated_at=updated_at
            )
            return Cursor(self)
        if normalized.startswith("UPDATE landscape_v4_run_stages SET status=%s"):
            status, completed, completed_at, updated_at, _run_id, stage_name = params
            self.stages[stage_name].update(
                status=status,
                completed_count=completed,
                completed_at=completed_at,
                updated_at=updated_at,
            )
            return Cursor(self)
        if normalized.startswith("UPDATE landscape_v4_run_stages SET status='FAILED'"):
            code, message, completed_at, updated_at, _run_id, stage_name = params
            self.stages[stage_name].update(
                status="FAILED",
                error_code=code,
                error_message=message,
                completed_at=completed_at,
                updated_at=updated_at,
            )
            return Cursor(self)
        if normalized.startswith("INSERT INTO landscape_v4_run_limitations"):
            keys = (
                "run_id", "limitation_id", "stage_name", "code", "message",
                "affected_count", "created_at",
            )
            self.limitations.setdefault(params[1], dict(zip(keys, params, strict=True)))
            return Cursor(self)
        if "FROM landscape_v4_run_limitations" in normalized:
            if "limitation_id=%s" in normalized:
                return Cursor(self, self.limitations.get(params[1]))
            return Cursor(
                self,
                rows=sorted(
                    self.limitations.values(),
                    key=lambda row: (row["created_at"], row["limitation_id"]),
                ),
            )
        raise AssertionError(normalized)


class LandscapeStageRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.connection = Connection()

        @contextmanager
        def connect():
            yield self.connection

        self.repository = PostgreSQLStageRepository("postgresql://fixture", connect=connect)

    def test_stage_plan_is_stable_serial_and_progress_is_monotonic(self):
        stages = self.repository.ensure("run")
        self.assertEqual(tuple(stage.stage_name for stage in stages), STAGE_ORDER)
        self.assertEqual(self.repository.ensure("run"), stages)
        started = self.repository.start(
            "run", V4StageName.ESTIMATE_SCALE, total_count=10
        )
        self.assertEqual(started.status, V4StageStatus.RUNNING)
        self.assertEqual(started.attempt, 1)
        progress = self.repository.progress(
            "run", V4StageName.ESTIMATE_SCALE, completed_count=4
        )
        self.assertEqual(progress.completed_count, 4)
        with self.assertRaisesRegex(StagePersistenceError, "backwards"):
            self.repository.progress(
                "run", V4StageName.ESTIMATE_SCALE, completed_count=3
            )
        succeeded = self.repository.succeed("run", V4StageName.ESTIMATE_SCALE)
        self.assertEqual(succeeded.status, V4StageStatus.SUCCEEDED)
        next_stage = self.repository.start("run", V4StageName.RETRIEVE_PAGES)
        self.assertEqual(next_stage.status, V4StageStatus.RUNNING)

    def test_stage_cannot_skip_incomplete_predecessor(self):
        self.repository.ensure("run")
        with self.assertRaisesRegex(StagePersistenceError, "predecessors"):
            self.repository.start("run", V4StageName.FETCH_ABSTRACTS)

    def test_failure_can_retry_without_losing_attempt_count(self):
        self.repository.ensure("run")
        self.repository.start("run", V4StageName.ESTIMATE_SCALE)
        failed = self.repository.fail(
            "run",
            V4StageName.ESTIMATE_SCALE,
            error_code="PROVIDER_TIMEOUT",
            error_message="timeout",
        )
        self.assertEqual(failed.status, V4StageStatus.FAILED)
        retried = self.repository.start("run", V4StageName.ESTIMATE_SCALE)
        self.assertEqual(retried.status, V4StageStatus.RUNNING)
        self.assertEqual(retried.attempt, 2)
        self.assertIsNone(retried.error_code)

    def test_limitations_are_append_only_idempotent_and_visible(self):
        self.repository.ensure("run")
        first = self.repository.add_limitation(
            "run",
            V4StageName.FETCH_ABSTRACTS,
            code="ABSTRACT_MISSING",
            message="一件专利缺少摘要",
            affected_count=1,
        )
        second = self.repository.add_limitation(
            "run",
            V4StageName.FETCH_ABSTRACTS,
            code="ABSTRACT_MISSING",
            message="一件专利缺少摘要",
            affected_count=1,
        )
        self.assertEqual(first.limitation_id, second.limitation_id)
        self.assertEqual(self.repository.limitations("run"), (first,))


if __name__ == "__main__":
    unittest.main()
