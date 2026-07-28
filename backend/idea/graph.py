from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from .database import Database
from .runtime_debug import RunDebugLog
from .workflow import (
    CompletionGateError,
    NonRetryableWorkflowError,
    WorkflowHarness,
    WorkflowStep,
    WORKFLOW_STEPS,
)


class IdeaGraphState(TypedDict):
    """Durable orchestration state. Credentials and patent payloads never enter it."""

    run_id: str
    last_completed_step: str | None
    completed_steps: int


StepHandler = Callable[[str, WorkflowStep, int], Awaitable[dict[str, Any]]]
FingerprintBuilder = Callable[[str, WorkflowStep], dict[str, Any]]
LimitationCollector = Callable[[str], list[dict[str, Any]]]


def _retry_transient_error(error: Exception) -> bool:
    return not isinstance(error, NonRetryableWorkflowError)


class LangGraphWorkflow:
    """Fixed 11-node graph; business state is persisted by the PostgreSQL harness."""

    def __init__(
        self,
        *,
        database: Database,
        harness: WorkflowHarness,
        step_handler: StepHandler,
        fingerprint_builder: FingerprintBuilder,
        limitation_collector: LimitationCollector,
        step_timeout_seconds: int,
        max_step_attempts: int,
        debug_log: RunDebugLog | None = None,
    ):
        self.database = database
        self.harness = harness
        self.step_handler = step_handler
        self.fingerprint_builder = fingerprint_builder
        self.limitation_collector = limitation_collector
        self.step_timeout_seconds = step_timeout_seconds
        self.max_step_attempts = max_step_attempts
        self.debug_log = debug_log
        self._initialize_lock = asyncio.Lock()
        self._graph = None

    async def execute(self, run_id: str) -> str:
        run = self.database.get_run(run_id)
        if run["status"] == "QUEUED":
            self.harness.begin_run(run_id)
            self._debug(run_id, "langgraph_started", total_nodes=len(WORKFLOW_STEPS))
        elif run["status"] != "RUNNING":
            return run["status"]

        graph = await self._compiled_graph()
        try:
            await graph.ainvoke(
                {"run_id": run_id, "last_completed_step": None, "completed_steps": 0}
            )
        except asyncio.CancelledError:
            if self.database.get_run(run_id)["status"] == "RUNNING":
                self.harness.cancel_run(run_id)
            self._debug(run_id, "langgraph_cancelled")
            raise
        except Exception as exc:
            run = self.database.get_run(run_id)
            if run["status"] == "RUNNING":
                self.database.set_run_status(
                    run_id,
                    "FAILED",
                    error_code=type(exc).__name__,
                    error_message=str(exc)[:2000],
                )
            self._debug(
                run_id,
                "langgraph_failed",
                error_code=type(exc).__name__,
                error_message=str(exc)[:1000],
            )
            return "FAILED"

        try:
            status = self.harness.finish_run(
                run_id, limitations=self.limitation_collector(run_id)
            )
        except CompletionGateError as exc:
            self.database.set_run_status(
                run_id,
                "FAILED",
                error_code="COMPLETION_GATE_FAILED",
                error_message=str(exc),
            )
            self._debug(
                run_id,
                "langgraph_failed",
                error_code="COMPLETION_GATE_FAILED",
                error_message=str(exc)[:1000],
            )
            return "FAILED"
        self._debug(run_id, "langgraph_finished", status=status)
        return status

    async def aclose(self) -> None:
        self._graph = None

    async def _compiled_graph(self):
        if self._graph is not None:
            return self._graph
        async with self._initialize_lock:
            if self._graph is not None:
                return self._graph
            self._graph = self._build_graph().compile(name="aifpatent-idea-workflow")
            return self._graph

    def _build_graph(self) -> StateGraph:
        builder = StateGraph(IdeaGraphState)
        retry = RetryPolicy(
            max_attempts=self.max_step_attempts,
            retry_on=_retry_transient_error,
        )
        previous: str | None = None
        for step in WORKFLOW_STEPS:
            node_name = step.value
            builder.add_node(node_name, self._node(step), retry_policy=retry)
            builder.add_edge(START if previous is None else previous, node_name)
            previous = node_name
        if previous is not None:
            builder.add_edge(previous, END)
        return builder

    def _node(self, step: WorkflowStep):
        async def run_node(state: IdeaGraphState) -> dict[str, Any]:
            run_id = state["run_id"]
            progress = self.harness.progress(run_id)
            step_state = next(item for item in progress["steps"] if item["name"] == step.value)
            if step_state["status"] == "SUCCEEDED":
                return {
                    "last_completed_step": step.value,
                    "completed_steps": progress["completed_steps"],
                }

            attempt = self.harness.start_step(
                run_id,
                step,
                self.fingerprint_builder(run_id, step),
            )
            self._debug(
                run_id,
                "langgraph_node_started",
                node_name=step.value,
                attempt=attempt,
            )
            try:
                output = await asyncio.wait_for(
                    self.step_handler(run_id, step, attempt),
                    timeout=self.step_timeout_seconds,
                )
            except asyncio.CancelledError:
                self._debug(run_id, "langgraph_node_cancelled", node_name=step.value)
                raise
            except Exception as exc:
                self.harness.fail_step(
                    run_id,
                    step,
                    attempt,
                    error_code=type(exc).__name__,
                    error_message=str(exc),
                )
                self._debug(
                    run_id,
                    "langgraph_node_failed",
                    node_name=step.value,
                    attempt=attempt,
                    error_code=type(exc).__name__,
                    error_message=str(exc)[:1000],
                )
                raise

            self.harness.complete_step(run_id, step, attempt, output)
            progress = self.harness.progress(run_id)
            self._debug(
                run_id,
                "langgraph_node_completed",
                node_name=step.value,
                attempt=attempt,
            )
            return {
                "last_completed_step": step.value,
                "completed_steps": progress["completed_steps"],
            }

        run_node.__name__ = f"run_{step.value.lower()}"
        return run_node

    def _debug(self, run_id: str, event: str, **details: Any) -> None:
        if self.debug_log:
            self.debug_log.append(run_id, event, **details)
