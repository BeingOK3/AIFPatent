from __future__ import annotations

import asyncio
import inspect
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, TypedDict

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from .followup import FollowupError, TERMINAL_TURN_STATUSES, TurnStatus
from .postgres_followup import PostgreSQLFollowupRepository


class FollowupWorkflowStep(str, Enum):
    PREPARE_FOLLOWUP_SCOPE = "PREPARE_FOLLOWUP_SCOPE"
    CLASSIFY_AND_PLAN = "CLASSIFY_AND_PLAN"
    RETRIEVE_FOLLOWUP_EVIDENCE = "RETRIEVE_FOLLOWUP_EVIDENCE"
    ASSEMBLE_FOLLOWUP_CONTEXT = "ASSEMBLE_FOLLOWUP_CONTEXT"
    GENERATE_FOLLOWUP_ANSWER = "GENERATE_FOLLOWUP_ANSWER"
    VERIFY_FOLLOWUP_ANSWER = "VERIFY_FOLLOWUP_ANSWER"
    PERSIST_FOLLOWUP_RESPONSE = "PERSIST_FOLLOWUP_RESPONSE"


FOLLOWUP_WORKFLOW_STEPS = tuple(FollowupWorkflowStep)


class FollowupGraphState(TypedDict):
    turn_id: str
    thread_id: str
    run_id: str
    last_completed_step: str | None
    completed_steps: int


class FollowupStepHandler(Protocol):
    async def execute(
        self, turn_id: str, step: FollowupWorkflowStep, attempt: int
    ) -> None: ...


class FollowupWorkflow:
    """Fixed short graph; business payloads remain in repositories and request memory."""

    def __init__(
        self,
        *,
        repository: PostgreSQLFollowupRepository,
        handler: FollowupStepHandler,
        checkpoint_path: Path,
        step_timeout_seconds: int = 300,
        max_step_attempts: int = 3,
    ) -> None:
        if step_timeout_seconds < 1 or max_step_attempts < 1:
            raise ValueError("follow-up workflow timeout and attempts must be positive")
        self.repository = repository
        self.handler = handler
        self.checkpoint_path = checkpoint_path
        self.step_timeout_seconds = step_timeout_seconds
        self.max_step_attempts = max_step_attempts
        self._initialize_lock = asyncio.Lock()
        self._saver_context = None
        self._saver: AsyncSqliteSaver | None = None
        self._graph = None
        self._attempts: dict[tuple[str, FollowupWorkflowStep], int] = {}

    async def execute(self, turn_id: str) -> TurnStatus:
        turn = await self.repository.get_turn(turn_id)
        if turn is None:
            raise FollowupError("follow-up Turn does not exist")
        if turn.status in TERMINAL_TURN_STATUSES:
            return turn.status
        if turn.status == TurnStatus.QUEUED:
            turn = await self.repository.start_turn(turn_id)
        if turn.status != TurnStatus.RUNNING:
            raise FollowupError("follow-up Turn is not executable")
        thread = await self.repository.get_thread(turn.thread_id)
        if thread is None:
            raise FollowupError("follow-up Thread does not exist")

        graph = await self._compiled_graph()
        try:
            await graph.ainvoke(
                {
                    "turn_id": turn.turn_id,
                    "thread_id": turn.thread_id,
                    "run_id": thread.run_id,
                    "last_completed_step": None,
                    "completed_steps": 0,
                },
                {"configurable": {"thread_id": f"followup:{turn.turn_id}"}},
            )
        except asyncio.CancelledError:
            current = await self.repository.get_turn(turn_id)
            if current is not None and current.status == TurnStatus.RUNNING:
                await self.repository.cancel_turn(turn_id)
            await self._release_turn(turn_id)
            raise
        except Exception as exc:
            current = await self.repository.get_turn(turn_id)
            if current is not None and current.status == TurnStatus.RUNNING:
                await self.repository.fail_turn(
                    turn_id,
                    error_code=type(exc).__name__,
                    error_message=str(exc) or "follow-up workflow failed",
                )
            await self._release_turn(turn_id)
            return TurnStatus.FAILED

        completed = await self.repository.get_turn(turn_id)
        if completed is None:
            raise FollowupError("follow-up Turn disappeared after workflow execution")
        if completed.status not in {
            TurnStatus.COMPLETED,
            TurnStatus.COMPLETED_WITH_LIMITATIONS,
        }:
            if completed.status == TurnStatus.RUNNING:
                await self.repository.fail_turn(
                    turn_id,
                    error_code="PERSIST_FOLLOWUP_RESPONSE_INCOMPLETE",
                    error_message="final workflow node did not persist a terminal response",
                )
            await self._release_turn(turn_id)
            return TurnStatus.FAILED
        await self._release_turn(turn_id)
        return completed.status

    async def aclose(self) -> None:
        if self._saver_context is not None:
            await self._saver_context.__aexit__(None, None, None)
            self._saver_context = None
            self._saver = None
            self._graph = None

    async def _compiled_graph(self):
        if self._graph is not None:
            return self._graph
        async with self._initialize_lock:
            if self._graph is not None:
                return self._graph
            self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            self._saver_context = AsyncSqliteSaver.from_conn_string(
                str(self.checkpoint_path)
            )
            self._saver = await self._saver_context.__aenter__()
            await self._saver.setup()
            self._graph = self._build_graph().compile(
                checkpointer=self._saver,
                name="aifpatent-followup-workflow",
            )
            return self._graph

    def _build_graph(self) -> StateGraph:
        builder = StateGraph(FollowupGraphState)
        retry = RetryPolicy(
            max_attempts=self.max_step_attempts,
            retry_on=Exception,
        )
        previous: str | None = None
        for step in FOLLOWUP_WORKFLOW_STEPS:
            builder.add_node(step.value, self._node(step), retry_policy=retry)
            builder.add_edge(START if previous is None else previous, step.value)
            previous = step.value
        if previous is not None:
            builder.add_edge(previous, END)
        return builder

    def _node(self, step: FollowupWorkflowStep):
        async def run_node(state: FollowupGraphState) -> dict[str, Any]:
            turn_id = state["turn_id"]
            key = (turn_id, step)
            attempt = self._attempts.get(key, 0) + 1
            self._attempts[key] = attempt
            await asyncio.wait_for(
                self.handler.execute(turn_id, step, attempt),
                timeout=self.step_timeout_seconds,
            )
            return {
                "last_completed_step": step.value,
                "completed_steps": state["completed_steps"] + 1,
            }

        return run_node

    def _clear_attempts(self, turn_id: str) -> None:
        for key in [value for value in self._attempts if value[0] == turn_id]:
            self._attempts.pop(key, None)

    async def _release_turn(self, turn_id: str) -> None:
        self._clear_attempts(turn_id)
        discard = getattr(self.handler, "discard", None)
        if discard is not None:
            result = discard(turn_id)
            if inspect.isawaitable(result):
                await result


__all__ = [
    "FOLLOWUP_WORKFLOW_STEPS",
    "FollowupGraphState",
    "FollowupStepHandler",
    "FollowupWorkflow",
    "FollowupWorkflowStep",
]
