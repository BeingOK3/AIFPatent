from __future__ import annotations

import asyncio

from idea.model_client import runtime_model_config

from .credential_lease import CredentialLeaseError
from .stage_repository import V4StageStatus
from .v4_run import LandscapeRunStatus
from .v4_workflow import V4WorkflowOutcome


_TERMINAL = {
    LandscapeRunStatus.COMPLETED,
    LandscapeRunStatus.COMPLETED_WITH_LIMITATIONS,
    LandscapeRunStatus.FAILED,
    LandscapeRunStatus.CANCELLED,
}


class V4LandscapeTaskManager:
    def __init__(self, workflow, run_repository, stage_repository, credential_vault):
        self.workflow = workflow
        self.run_repository = run_repository
        self.stage_repository = stage_repository
        self.credential_vault = credential_vault
        self.tasks: dict[str, asyncio.Task] = {}

    def start(self, run_id: str) -> bool:
        run = self.run_repository.get(run_id)
        if run.status in _TERMINAL or run.status == LandscapeRunStatus.AWAITING_SCALE_CONFIRMATION:
            return False
        current = self.tasks.get(run_id)
        if current is not None and not current.done():
            return False
        self.tasks[run_id] = asyncio.create_task(
            self._run(run_id), name=f"landscape-v4:{run_id}"
        )
        return True

    async def _run(self, run_id: str) -> None:
        owner = f"v4-task:{id(asyncio.current_task())}"
        try:
            outcome = await self.workflow.execute_preanalysis(run_id)
            if outcome == V4WorkflowOutcome.AWAITING_SCALE_CONFIRMATION:
                return
            try:
                lease = self.credential_vault.acquire(run_id, owner)
            except CredentialLeaseError:
                run = self.run_repository.get(run_id)
                if run.status == LandscapeRunStatus.RUNNING:
                    self.run_repository.transition(
                        run_id,
                        LandscapeRunStatus.WAITING_FOR_CREDENTIALS,
                        expected=(LandscapeRunStatus.RUNNING,),
                    )
                return
            run = self.run_repository.get(run_id)
            if run.status == LandscapeRunStatus.WAITING_FOR_CREDENTIALS:
                self.run_repository.transition(
                    run_id,
                    LandscapeRunStatus.RUNNING,
                    expected=(LandscapeRunStatus.WAITING_FOR_CREDENTIALS,),
                )
            with runtime_model_config(lease.config):
                await self.workflow.execute_semantics(run_id)
            self.credential_vault.release(run_id, owner)
            self.workflow.execute_after_semantics(run_id)
            self.credential_vault.revoke(run_id)
        except asyncio.CancelledError:
            self.credential_vault.release(run_id, owner)
            raise
        except Exception as exc:
            self.credential_vault.release(run_id, owner)
            self._fail_current_stage(run_id, type(exc).__name__)
            try:
                run = self.run_repository.get(run_id)
                if run.status not in _TERMINAL:
                    self.run_repository.transition(
                        run_id,
                        LandscapeRunStatus.FAILED,
                        expected=(run.status,),
                        error_code=type(exc).__name__,
                        error_message="Landscape v4 stage execution failed.",
                    )
            except KeyError:
                pass
        finally:
            if self.tasks.get(run_id) is asyncio.current_task():
                self.tasks.pop(run_id, None)

    def _fail_current_stage(self, run_id: str, error_code: str) -> None:
        try:
            running = next(
                stage
                for stage in self.stage_repository.list(run_id)
                if stage.status == V4StageStatus.RUNNING
            )
        except (KeyError, StopIteration):
            return
        self.stage_repository.fail(
            run_id,
            running.stage_name,
            error_code=error_code,
            error_message="Stage execution failed.",
        )

    async def cancel(self, run_id: str) -> bool:
        run = self.run_repository.get(run_id)
        if run.status in _TERMINAL:
            return False
        task = self.tasks.get(run_id)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.run_repository.cancel(run_id)
        self.credential_vault.revoke(run_id)
        return True

    async def aclose(self) -> None:
        # Process shutdown is not a user cancellation. Leave durable Run and
        # stage states intact so the next worker can resume from checkpoints.
        # Credentials are memory-only and must be supplied again after restart.
        pending = list(self.tasks.items())
        for _run_id, task in pending:
            if not task.done():
                task.cancel()
        if pending:
            await asyncio.gather(
                *(task for _run_id, task in pending), return_exceptions=True
            )
        for run_id, _task in pending:
            self.credential_vault.revoke(run_id)

    def resume_incomplete(self) -> int:
        resumed = 0
        for run in self.run_repository.list(500):
            if run.status in _TERMINAL or run.status == LandscapeRunStatus.AWAITING_SCALE_CONFIRMATION:
                continue
            resumed += self.start(run.run_id)
        return resumed


__all__ = ["V4LandscapeTaskManager"]
