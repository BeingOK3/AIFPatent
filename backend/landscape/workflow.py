from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from .database import LandscapeDatabase, canonical_json, now_ms
from .store import LandscapeRunStore, LandscapeStoreError


class LandscapeWorkflowStep(StrEnum):
    VALIDATE_SCOPE = "VALIDATE_SCOPE"
    PLAN_SEARCH = "PLAN_SEARCH"
    SEARCH_PUBLICATIONS = "SEARCH_PUBLICATIONS"
    FILTER_AND_SELECT = "FILTER_AND_SELECT"
    FETCH_DETAILS = "FETCH_DETAILS"
    ANALYZE_PATENTS = "ANALYZE_PATENTS"
    ANALYZE_COMPANIES = "ANALYZE_COMPANIES"
    ANALYZE_CROSS_COMPANY_TRENDS = "ANALYZE_CROSS_COMPANY_TRENDS"
    CLUSTER_PATENTS = "CLUSTER_PATENTS"
    BUILD_REPORT = "BUILD_REPORT"


WORKFLOW_STEPS = tuple(LandscapeWorkflowStep)
TERMINAL_STATUSES = {"COMPLETED", "COMPLETED_WITH_LIMITATIONS", "FAILED", "CANCELLED"}
MAIN_TASK_KEY = "__main__"


class LandscapeWorkflowError(RuntimeError):
    pass


class NonRetryableLandscapeWorkflowError(LandscapeWorkflowError):
    """A deterministic policy or data gate that cannot succeed on identical retry."""


def _retry_transient_error(error: Exception) -> bool:
    return not isinstance(error, NonRetryableLandscapeWorkflowError)


class LandscapeWorkflowState(TypedDict):
    run_id: str
    last_completed_step: str | None
    completed_steps: int


StepHandler = Callable[[str, LandscapeWorkflowStep, int], Awaitable[dict[str, Any]]]
LimitationCollector = Callable[[str], list[dict[str, Any]]]


class LandscapeWorkflowHarness:
    def __init__(
        self,
        database: LandscapeDatabase,
        store: LandscapeRunStore,
        *,
        max_step_attempts: int,
    ):
        self.database = database
        self.store = store
        self.max_step_attempts = max_step_attempts

    def begin_run(self, run_id: str) -> None:
        self.database.set_run_status(run_id, "RUNNING")

    def cancel_run(self, run_id: str) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE landscape_steps SET status='INTERRUPTED',completed_at=?,
                    error_code='RUN_CANCELLED',error_message='run cancelled'
                WHERE run_id=? AND status='RUNNING'
                """,
                (now_ms(), run_id),
            )
        self.database.set_run_status(run_id, "CANCELLED")

    def next_step(self, run_id: str) -> LandscapeWorkflowStep | None:
        latest = self._latest_steps(run_id)
        return next(
            (step for step in WORKFLOW_STEPS if latest.get(step.value, {}).get("status") != "SUCCEEDED"),
            None,
        )

    def start_step(
        self,
        run_id: str,
        step: LandscapeWorkflowStep,
        input_value: Any,
        *,
        task_key: str = MAIN_TASK_KEY,
    ) -> int:
        task_key = _validated_task_key(task_key)
        run = self.database.get_run(run_id)
        if run["status"] != "RUNNING":
            raise LandscapeWorkflowError(f"run is not RUNNING: {run['status']}")
        if task_key == MAIN_TASK_KEY:
            expected = self.next_step(run_id)
            if expected != step:
                raise LandscapeWorkflowError(
                    f"out-of-order step: expected {expected}, got {step}"
                )
        attempt = self._attempt_count(run_id, step, task_key) + 1
        if attempt > self.max_step_attempts:
            raise LandscapeWorkflowError("step attempt limit exceeded")
        encoded = canonical_json(input_value)
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO landscape_steps(
                    run_id,step_name,task_key,attempt,status,input_hash,started_at
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    run_id,
                    step.value,
                    task_key,
                    attempt,
                    "RUNNING",
                    _hash(encoded),
                    now_ms(),
                ),
            )
        return attempt

    def complete_step(
        self,
        run_id: str,
        step: LandscapeWorkflowStep,
        attempt: int,
        output: dict[str, Any],
        *,
        task_key: str = MAIN_TASK_KEY,
    ) -> None:
        task_key = _validated_task_key(task_key)
        encoded = canonical_json(output)
        if task_key == MAIN_TASK_KEY:
            self.database.put_stage_result(run_id, step.value, output)
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE landscape_steps SET status='SUCCEEDED',output_hash=?,output_json=?,completed_at=?
                WHERE run_id=? AND step_name=? AND task_key=?
                  AND attempt=? AND status='RUNNING'
                """,
                (
                    _hash(encoded),
                    encoded,
                    now_ms(),
                    run_id,
                    step.value,
                    task_key,
                    attempt,
                ),
            )
            if cursor.rowcount != 1:
                raise LandscapeWorkflowError("running step attempt not found")

    def fail_step(
        self,
        run_id: str,
        step: LandscapeWorkflowStep,
        attempt: int,
        error: Exception,
        *,
        task_key: str = MAIN_TASK_KEY,
    ) -> None:
        task_key = _validated_task_key(task_key)
        with self.database.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE landscape_steps SET status='FAILED',completed_at=?,error_code=?,error_message=?
                WHERE run_id=? AND step_name=? AND task_key=?
                  AND attempt=? AND status='RUNNING'
                """,
                (
                    now_ms(),
                    type(error).__name__,
                    str(error)[:2000],
                    run_id,
                    step.value,
                    task_key,
                    attempt,
                ),
            )
            if cursor.rowcount != 1:
                raise LandscapeWorkflowError("running step attempt not found")
        if task_key == MAIN_TASK_KEY and attempt >= self.max_step_attempts:
            self.database.set_run_status(
                run_id, "FAILED", error_code=type(error).__name__, error_message=str(error)[:2000]
            )

    def finish_run(self, run_id: str, limitations: list[dict[str, Any]]) -> str:
        issues = self.completion_issues(run_id)
        if issues:
            raise LandscapeWorkflowError("completion gates failed: " + "; ".join(issues))
        status = "COMPLETED_WITH_LIMITATIONS" if limitations else "COMPLETED"
        self.database.set_run_status(run_id, status, limitations=limitations)
        return status

    def completion_issues(self, run_id: str) -> list[str]:
        issues = []
        run = self.database.get_run(run_id)
        if run["status"] != "RUNNING":
            issues.append(f"run status is {run['status']}")
        latest = self._latest_steps(run_id)
        issues.extend(
            f"step not successful: {step.value}"
            for step in WORKFLOW_STEPS
            if latest.get(step.value, {}).get("status") != "SUCCEEDED"
        )
        try:
            self.store.verify(run_id)
        except LandscapeStoreError as exc:
            issues.append(str(exc))
        return issues

    def progress(self, run_id: str) -> dict[str, Any]:
        run = self.database.get_run(run_id)
        latest = self._latest_steps(run_id)
        completed = sum(
            latest.get(step.value, {}).get("status") == "SUCCEEDED" for step in WORKFLOW_STEPS
        )
        next_step = self.next_step(run_id)
        return {
            "run_id": run_id,
            "status": run["status"],
            "current_step": next_step.value if next_step else None,
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

    def latest_task(
        self,
        run_id: str,
        step: LandscapeWorkflowStep,
        *,
        task_key: str,
    ) -> dict[str, Any] | None:
        task_key = _validated_task_key(task_key)
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM landscape_steps
                WHERE run_id=? AND step_name=? AND task_key=?
                ORDER BY attempt DESC LIMIT 1
                """,
                (run_id, step.value, task_key),
            ).fetchone()
        return dict(row) if row is not None else None

    def _attempt_count(
        self,
        run_id: str,
        step: LandscapeWorkflowStep,
        task_key: str = MAIN_TASK_KEY,
    ) -> int:
        with self.database.connect() as connection:
            return connection.execute(
                """
                SELECT COUNT(*) FROM landscape_steps
                WHERE run_id=? AND step_name=? AND task_key=?
                """,
                (run_id, step.value, task_key),
            ).fetchone()[0]

    def _latest_steps(self, run_id: str) -> dict[str, dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT s.* FROM landscape_steps s JOIN (
                    SELECT step_name,MAX(attempt) attempt FROM landscape_steps
                    WHERE run_id=? AND task_key=? GROUP BY step_name
                ) latest ON latest.step_name=s.step_name AND latest.attempt=s.attempt
                WHERE s.run_id=? AND s.task_key=?
                """,
                (run_id, MAIN_TASK_KEY, run_id, MAIN_TASK_KEY),
            ).fetchall()
        return {row["step_name"]: dict(row) for row in rows}


class LandscapeWorkflow:
    def __init__(
        self,
        *,
        database: LandscapeDatabase,
        harness: LandscapeWorkflowHarness,
        step_handler: StepHandler,
        limitation_collector: LimitationCollector,
        step_timeout_seconds: int,
        max_step_attempts: int,
    ):
        self.database = database
        self.harness = harness
        self.step_handler = step_handler
        self.limitation_collector = limitation_collector
        self.step_timeout_seconds = step_timeout_seconds
        self.max_step_attempts = max_step_attempts
        self._lock = asyncio.Lock()
        self._graph = None

    async def execute(self, run_id: str) -> str:
        run = self.database.get_run(run_id)
        if run["status"] == "QUEUED":
            self.harness.begin_run(run_id)
        elif run["status"] != "RUNNING":
            return run["status"]
        graph = await self._compiled_graph()
        try:
            await graph.ainvoke(
                {"run_id": run_id, "last_completed_step": None, "completed_steps": 0}
            )
            return self.harness.finish_run(run_id, self.limitation_collector(run_id))
        except asyncio.CancelledError:
            if self.database.get_run(run_id)["status"] == "RUNNING":
                self.harness.cancel_run(run_id)
            raise
        except Exception as exc:
            current = self.database.get_run(run_id)
            if current["status"] == "RUNNING":
                self.database.set_run_status(
                    run_id, "FAILED", error_code=type(exc).__name__, error_message=str(exc)[:2000]
                )
            return "FAILED"

    async def aclose(self) -> None:
        self._graph = None

    async def _compiled_graph(self):
        if self._graph is not None:
            return self._graph
        async with self._lock:
            if self._graph is not None:
                return self._graph
            self._graph = self._build_graph().compile(name="aifpatent-landscape-workflow")
            return self._graph

    def _build_graph(self) -> StateGraph:
        builder = StateGraph(LandscapeWorkflowState)
        retry = RetryPolicy(
            max_attempts=self.max_step_attempts,
            retry_on=_retry_transient_error,
        )
        previous = None
        for step in WORKFLOW_STEPS:
            builder.add_node(step.value, self._node(step), retry_policy=retry)
            builder.add_edge(START if previous is None else previous, step.value)
            previous = step.value
        builder.add_edge(previous, END)
        return builder

    def _node(self, step: LandscapeWorkflowStep):
        async def node(state: LandscapeWorkflowState) -> dict[str, Any]:
            run_id = state["run_id"]
            progress = self.harness.progress(run_id)
            state_for_step = next(item for item in progress["steps"] if item["name"] == step.value)
            if state_for_step["status"] == "SUCCEEDED":
                return {"last_completed_step": step.value, "completed_steps": progress["completed_steps"]}
            attempt = self.harness.start_step(run_id, step, {"input_hash": self.database.get_run(run_id)["input_hash"]})
            try:
                output = await asyncio.wait_for(
                    self.step_handler(run_id, step, attempt), timeout=self.step_timeout_seconds
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.harness.fail_step(run_id, step, attempt, exc)
                raise
            self.harness.complete_step(run_id, step, attempt, output)
            return {"last_completed_step": step.value, "completed_steps": progress["completed_steps"] + 1}

        node.__name__ = f"run_{step.value.lower()}"
        return node


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _validated_task_key(task_key: str) -> str:
    if not isinstance(task_key, str) or task_key != task_key.strip():
        raise LandscapeWorkflowError("task_key must be a trimmed string")
    if not task_key or len(task_key) > 256:
        raise LandscapeWorkflowError(
            "task_key must contain between 1 and 256 characters"
        )
    return task_key
