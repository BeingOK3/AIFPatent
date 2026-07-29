from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any

from .database import Database, canonical_json, now_ms
from .run_store import RunStore, RunStoreError


class WorkflowStep(StrEnum):
    PREPARE_INPUT = "PREPARE_INPUT"
    PARSE_IDEA = "PARSE_IDEA"
    VALIDATE_IDEA_MODEL = "VALIDATE_IDEA_MODEL"
    PLAN_QUERIES = "PLAN_QUERIES"
    RETRIEVE_CANDIDATES = "RETRIEVE_CANDIDATES"
    NORMALIZE_AND_FETCH = "NORMALIZE_AND_FETCH"
    ANALYZE_DOCUMENTS = "ANALYZE_DOCUMENTS"
    DETERMINE_NOVELTY = "DETERMINE_NOVELTY"
    ANALYZE_INVENTIVENESS = "ANALYZE_INVENTIVENESS"
    ASSESS_VALUE = "ASSESS_VALUE"
    AUDIT_AND_REPORT = "AUDIT_AND_REPORT"


WORKFLOW_STEPS = tuple(WorkflowStep)
TERMINAL_RUN_STATUSES = {
    "COMPLETED",
    "COMPLETED_WITH_LIMITATIONS",
    "FAILED",
    "CANCELLED",
}
ALLOWED_RUN_TRANSITIONS = {
    "QUEUED": {"RUNNING", "CANCELLED"},
    "RUNNING": TERMINAL_RUN_STATUSES,
    "COMPLETED": set(),
    "COMPLETED_WITH_LIMITATIONS": set(),
    "FAILED": set(),
    "CANCELLED": set(),
}


class WorkflowError(RuntimeError):
    pass


class NonRetryableWorkflowError(WorkflowError):
    """A deterministic business gate that cannot succeed on an identical retry."""


class CompletionGateError(WorkflowError):
    def __init__(self, issues: list[str]):
        super().__init__("completion gates failed: " + "; ".join(issues))
        self.issues = issues


class WorkflowHarness:
    def __init__(self, database: Database, run_store: RunStore, *, max_step_attempts: int):
        self.database = database
        self.run_store = run_store
        self.max_step_attempts = max_step_attempts
        self.recovery_ready = False

    def recover_incomplete(self) -> list[str]:
        recovered = []
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT run_id FROM run_steps WHERE status = 'RUNNING'"
            ).fetchall()
            for row in rows:
                connection.execute(
                    """
                    UPDATE run_steps SET status = 'INTERRUPTED', completed_at = ?,
                        error_code = 'PROCESS_RESTART',
                        error_message = 'step was interrupted by process restart'
                    WHERE run_id = ? AND status = 'RUNNING'
                    """,
                    (now_ms(), row["run_id"]),
                )
                recovered.append(row["run_id"])
        self.recovery_ready = True
        return recovered

    def begin_run(self, run_id: str) -> None:
        self._transition_run(run_id, "RUNNING")

    def cancel_run(self, run_id: str) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE run_steps SET status = 'INTERRUPTED', completed_at = ?,
                    error_code = 'RUN_CANCELLED', error_message = 'run cancelled'
                WHERE run_id = ? AND status = 'RUNNING'
                """,
                (now_ms(), run_id),
            )
        self._transition_run(run_id, "CANCELLED")

    def next_step(self, run_id: str) -> WorkflowStep | None:
        latest = self._latest_steps(run_id)
        for step in WORKFLOW_STEPS:
            if latest.get(step.value, {}).get("status") != "SUCCEEDED":
                return step
        return None

    def start_step(self, run_id: str, step: WorkflowStep, input_value: Any) -> int:
        run = self.database.get_run(run_id)
        if run["status"] != "RUNNING":
            raise WorkflowError(f"run is not RUNNING: {run['status']}")
        expected = self.next_step(run_id)
        if expected != step:
            raise WorkflowError(f"out-of-order step: expected {expected}, got {step}")
        latest = self._latest_steps(run_id).get(step.value)
        if latest and latest["status"] == "RUNNING":
            raise WorkflowError("step already running")
        attempt = self._attempt_count(run_id, step) + 1
        if attempt > self.max_step_attempts:
            raise WorkflowError("step attempt limit exceeded")
        encoded = self._encoded(input_value)
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO run_steps(
                    run_id,step_name,attempt,status,input_hash,started_at
                ) VALUES(?,?,?,?,?,?)
                """,
                (run_id, step.value, attempt, "RUNNING", self._hash(encoded), now_ms()),
            )
        return attempt

    def complete_step(
        self, run_id: str, step: WorkflowStep, attempt: int, output_value: Any
    ) -> None:
        encoded = self._encoded(output_value)
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE run_steps SET status = 'SUCCEEDED', output_hash = ?, output_json = ?,
                    completed_at = ?, error_code = NULL, error_message = NULL
                WHERE run_id = ? AND step_name = ? AND attempt = ? AND status = 'RUNNING'
                """,
                (self._hash(encoded), encoded, now_ms(), run_id, step.value, attempt),
            )
            if cursor.rowcount != 1:
                raise WorkflowError("running step attempt not found")

    def fail_step(
        self,
        run_id: str,
        step: WorkflowStep,
        attempt: int,
        *,
        error_code: str,
        error_message: str,
    ) -> None:
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE run_steps SET status = 'FAILED', completed_at = ?,
                    error_code = ?, error_message = ?
                WHERE run_id = ? AND step_name = ? AND attempt = ? AND status = 'RUNNING'
                """,
                (now_ms(), error_code, error_message[:2000], run_id, step.value, attempt),
            )
            if cursor.rowcount != 1:
                raise WorkflowError("running step attempt not found")
        if attempt >= self.max_step_attempts:
            self._transition_run(
                run_id,
                "FAILED",
                error_code=error_code,
                error_message=error_message[:2000],
            )

    def finish_run(self, run_id: str, *, limitations: list[dict[str, Any]]) -> str:
        issues = self.completion_issues(run_id)
        if issues:
            raise CompletionGateError(issues)
        status = "COMPLETED_WITH_LIMITATIONS" if limitations else "COMPLETED"
        self._transition_run(run_id, status, limitations=limitations)
        return status

    def completion_issues(self, run_id: str) -> list[str]:
        issues = []
        run = self.database.get_run(run_id)
        if run["status"] != "RUNNING":
            issues.append(f"run status is {run['status']}, expected RUNNING")
        latest = self._latest_steps(run_id)
        for step in WORKFLOW_STEPS:
            if latest.get(step.value, {}).get("status") != "SUCCEEDED":
                issues.append(f"step not successful: {step.value}")
        with self.database.connect() as connection:
            critical = connection.execute(
                "SELECT COUNT(*) FROM audit_results WHERE run_id = ? AND severity = 'critical'",
                (run_id,),
            ).fetchone()[0]
        if critical:
            issues.append(f"critical audit issues: {critical}")
        try:
            self.run_store.verify(run["case_id"], run_id)
        except RunStoreError as exc:
            issues.append(str(exc))
        return issues

    def progress(self, run_id: str) -> dict[str, Any]:
        run = self.database.get_run(run_id)
        latest = self._latest_steps(run_id)
        completed = sum(
            1 for step in WORKFLOW_STEPS if latest.get(step.value, {}).get("status") == "SUCCEEDED"
        )
        return {
            "run_id": run_id,
            "status": run["status"],
            "current_step": self.next_step(run_id).value if self.next_step(run_id) else None,
            "completed_steps": completed,
            "total_steps": len(WORKFLOW_STEPS),
            "steps": [
                {
                    "name": step.value,
                    "status": latest.get(step.value, {}).get("status", "PENDING"),
                    "attempt": latest.get(step.value, {}).get("attempt", 0),
                    "error_code": latest.get(step.value, {}).get("error_code"),
                }
                for step in WORKFLOW_STEPS
            ],
        }

    def _transition_run(
        self,
        run_id: str,
        target: str,
        *,
        limitations: list[dict[str, Any]] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        run = self.database.get_run(run_id)
        if target not in ALLOWED_RUN_TRANSITIONS[run["status"]]:
            raise WorkflowError(f"illegal run transition: {run['status']} -> {target}")
        self.database.set_run_status(
            run_id,
            target,
            limitations=limitations,
            error_code=error_code,
            error_message=error_message,
        )

    def _attempt_count(self, run_id: str, step: WorkflowStep) -> int:
        with self.database.connect() as connection:
            return connection.execute(
                "SELECT COUNT(*) FROM run_steps WHERE run_id = ? AND step_name = ?",
                (run_id, step.value),
            ).fetchone()[0]

    def _latest_steps(self, run_id: str) -> dict[str, dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT s.* FROM run_steps s
                JOIN (
                    SELECT step_name, MAX(attempt) AS attempt FROM run_steps
                    WHERE run_id = ? GROUP BY step_name
                ) latest ON latest.step_name = s.step_name AND latest.attempt = s.attempt
                WHERE s.run_id = ?
                """,
                (run_id, run_id),
            ).fetchall()
        return {row["step_name"]: dict(row) for row in rows}

    @staticmethod
    def _encoded(value: Any) -> str:
        return canonical_json(value)

    @staticmethod
    def _hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
