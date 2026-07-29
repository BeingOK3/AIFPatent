from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from idea.followup import FollowupError, TurnStatus
from idea.followup_workflow import (
    FOLLOWUP_WORKFLOW_STEPS,
    FollowupGraphState,
    FollowupWorkflow,
)


class MemoryRepository:
    def __init__(self, status=TurnStatus.QUEUED) -> None:
        self.turn = SimpleNamespace(
            turn_id="turn-1", thread_id="thread-1", status=status
        )
        self.thread = SimpleNamespace(thread_id="thread-1", run_id="run-1")
        self.failures = []
        self.cancelled = []

    async def get_turn(self, turn_id):
        return self.turn if self.turn is not None and turn_id == self.turn.turn_id else None

    async def get_thread(self, thread_id):
        return self.thread if thread_id == self.thread.thread_id else None

    async def start_turn(self, turn_id):
        self.turn.status = TurnStatus.RUNNING
        return self.turn

    async def complete_turn(self, turn_id, *, answer, limitations=()):
        self.turn.status = (
            TurnStatus.COMPLETED_WITH_LIMITATIONS
            if limitations
            else TurnStatus.COMPLETED
        )
        return self.turn

    async def fail_turn(self, turn_id, *, error_code, error_message):
        self.failures.append((error_code, error_message))
        self.turn.status = TurnStatus.FAILED
        return self.turn

    async def cancel_turn(self, turn_id):
        self.cancelled.append(turn_id)
        self.turn.status = TurnStatus.CANCELLED
        return self.turn


class RecordingHandler:
    def __init__(self, repository, *, fail_step=None, block_step=None) -> None:
        self.repository = repository
        self.fail_step = fail_step
        self.block_step = block_step
        self.calls = []
        self.discarded = []

    async def execute(self, turn_id, step, attempt):
        self.calls.append((step, attempt))
        if step == self.fail_step:
            raise RuntimeError("controlled step failure")
        if step == self.block_step:
            await asyncio.Event().wait()
        if step == FOLLOWUP_WORKFLOW_STEPS[-1]:
            await self.repository.complete_turn(
                turn_id, answer={"answer_type": "DIRECT", "direct_answer": "完成"}
            )

    def discard(self, turn_id):
        self.discarded.append(turn_id)


class FollowupWorkflowTests(unittest.TestCase):
    def run_workflow(self, repository, handler, *, timeout=1):
        async def scenario():
            workflow = FollowupWorkflow(
                repository=repository,
                handler=handler,
                step_timeout_seconds=timeout,
                max_step_attempts=1,
            )
            try:
                return await workflow.execute("turn-1")
            finally:
                await workflow.aclose()

        return asyncio.run(scenario())

    def test_fixed_seven_steps_complete_in_declared_order(self) -> None:
        repository = MemoryRepository()
        handler = RecordingHandler(repository)

        status = self.run_workflow(repository, handler)

        self.assertEqual(status, TurnStatus.COMPLETED)
        self.assertEqual([item[0] for item in handler.calls], list(FOLLOWUP_WORKFLOW_STEPS))
        self.assertTrue(all(attempt == 1 for _, attempt in handler.calls))
        self.assertEqual(handler.discarded, ["turn-1"])

    def test_checkpoint_state_contract_contains_identifiers_and_progress_only(self) -> None:
        self.assertEqual(
            set(FollowupGraphState.__annotations__),
            {
                "turn_id", "thread_id", "run_id",
                "last_completed_step", "completed_steps",
            },
        )

    def test_step_failure_marks_only_current_turn_failed(self) -> None:
        repository = MemoryRepository()
        handler = RecordingHandler(repository, fail_step=FOLLOWUP_WORKFLOW_STEPS[2])

        status = self.run_workflow(repository, handler)

        self.assertEqual(status, TurnStatus.FAILED)
        self.assertEqual(repository.failures[0][0], "RuntimeError")
        self.assertNotIn(FOLLOWUP_WORKFLOW_STEPS[3], [item[0] for item in handler.calls])
        self.assertEqual(handler.discarded, ["turn-1"])

    def test_terminal_turn_is_idempotent_and_does_not_run_handlers(self) -> None:
        repository = MemoryRepository(TurnStatus.COMPLETED_WITH_LIMITATIONS)
        handler = RecordingHandler(repository)

        status = self.run_workflow(repository, handler)

        self.assertEqual(status, TurnStatus.COMPLETED_WITH_LIMITATIONS)
        self.assertEqual(handler.calls, [])

    def test_missing_turn_fails_before_checkpoint_or_handler(self) -> None:
        repository = MemoryRepository()
        repository.turn = None
        handler = RecordingHandler(repository)
        with self.assertRaisesRegex(FollowupError, "does not exist"):
            self.run_workflow(repository, handler)

    def test_final_node_must_persist_a_completed_response(self) -> None:
        repository = MemoryRepository()

        class NoopHandler:
            async def execute(self, turn_id, step, attempt):
                return None

        status = self.run_workflow(repository, NoopHandler())

        self.assertEqual(status, TurnStatus.FAILED)
        self.assertEqual(
            repository.failures[0][0], "PERSIST_FOLLOWUP_RESPONSE_INCOMPLETE"
        )

    def test_cancellation_marks_only_running_turn_cancelled(self) -> None:
        async def scenario():
            repository = MemoryRepository()
            started = asyncio.Event()

            class BlockingHandler:
                async def execute(self, turn_id, step, attempt):
                    started.set()
                    await asyncio.Event().wait()

            workflow = FollowupWorkflow(
                repository=repository,
                handler=BlockingHandler(),
                step_timeout_seconds=30,
                max_step_attempts=1,
            )
            task = asyncio.create_task(workflow.execute("turn-1"))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            await workflow.aclose()
            return repository

        repository = asyncio.run(scenario())
        self.assertEqual(repository.turn.status, TurnStatus.CANCELLED)
        self.assertEqual(repository.cancelled, ["turn-1"])


if __name__ == "__main__":
    unittest.main()
