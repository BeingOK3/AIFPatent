from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from idea.model_client import RuntimeModelConfig
from landscape.credential_lease import CredentialVault
from landscape.stage_repository import V4StageStatus
from landscape.v4_run import LandscapeRunStatus
from landscape.v4_tasks import V4LandscapeTaskManager
from landscape.v4_workflow import V4WorkflowOutcome


class RunRepository:
    def __init__(self):
        self.value = SimpleNamespace(status=LandscapeRunStatus.RUNNING)
        self.error = None

    def get(self, run_id):
        return self.value

    def transition(self, run_id, target, *, expected, error_code=None, error_message=None):
        if self.value.status not in expected:
            raise ValueError("bad transition")
        self.value = SimpleNamespace(status=LandscapeRunStatus(target))
        self.error = (error_code, error_message)
        return self.value

    def cancel(self, run_id):
        self.value = SimpleNamespace(status=LandscapeRunStatus.CANCELLED)
        return self.value


class StageRepository:
    def __init__(self):
        self.stage = SimpleNamespace(
            stage_name="EXTRACT_DIRECTIONS", status=V4StageStatus.PENDING
        )
        self.failure = None

    def list(self, run_id):
        return (self.stage,)

    def fail(self, run_id, stage_name, *, error_code, error_message):
        self.failure = (error_code, error_message)
        self.stage = SimpleNamespace(stage_name=stage_name, status=V4StageStatus.FAILED)


class Workflow:
    def __init__(self, runs, *, fail=False):
        self.runs = runs
        self.fail = fail
        self.calls = []

    async def execute_preanalysis(self, run_id):
        self.calls.append("preanalysis")
        return V4WorkflowOutcome.PREANALYSIS_COMPLETE

    async def execute_semantics(self, run_id):
        self.calls.append("semantics")
        if self.fail:
            raise RuntimeError("secret-value must never persist")

    def execute_after_semantics(self, run_id):
        self.calls.append("finalize")
        self.runs.transition(
            run_id,
            LandscapeRunStatus.COMPLETED,
            expected=(LandscapeRunStatus.RUNNING,),
        )


class BlockingWorkflow(Workflow):
    def __init__(self, runs):
        super().__init__(runs)
        self.started = asyncio.Event()

    async def execute_semantics(self, run_id):
        self.calls.append("semantics")
        self.started.set()
        await asyncio.Event().wait()


class V4LandscapeTaskManagerTests(unittest.TestCase):
    def test_process_shutdown_preserves_run_for_restart_recovery(self):
        async def scenario():
            runs = RunRepository()
            stages = StageRepository()
            vault = CredentialVault()
            vault.put(
                "RUN-1",
                RuntimeModelConfig(
                    base_url="https://example.test/v1",
                    api_key="secret-value",
                    model="fixture",
                ),
            )
            workflow = BlockingWorkflow(runs)
            manager = V4LandscapeTaskManager(workflow, runs, stages, vault)
            manager.start("RUN-1")
            await workflow.started.wait()
            await manager.aclose()
            return runs, manager, vault

        runs, manager, vault = asyncio.run(scenario())
        self.assertEqual(runs.value.status, LandscapeRunStatus.RUNNING)
        self.assertEqual(manager.tasks, {})
        self.assertFalse(vault.has_credentials("RUN-1"))

    def test_missing_credentials_waits_after_provider_preanalysis(self):
        async def scenario():
            runs = RunRepository()
            stages = StageRepository()
            workflow = Workflow(runs)
            manager = V4LandscapeTaskManager(workflow, runs, stages, CredentialVault())
            self.assertTrue(manager.start("RUN-1"))
            task = manager.tasks["RUN-1"]
            await task
            return runs, workflow

        runs, workflow = asyncio.run(scenario())
        self.assertEqual(runs.value.status, LandscapeRunStatus.WAITING_FOR_CREDENTIALS)
        self.assertEqual(workflow.calls, ["preanalysis"])

    def test_credentials_resume_semantics_and_are_revoked_on_completion(self):
        async def scenario():
            runs = RunRepository()
            stages = StageRepository()
            vault = CredentialVault()
            vault.put(
                "RUN-1",
                RuntimeModelConfig(
                    base_url="https://example.test/v1",
                    api_key="secret-value",
                    model="fixture",
                ),
            )
            workflow = Workflow(runs)
            manager = V4LandscapeTaskManager(workflow, runs, stages, vault)
            manager.start("RUN-1")
            await manager.tasks["RUN-1"]
            return runs, workflow, vault

        runs, workflow, vault = asyncio.run(scenario())
        self.assertEqual(runs.value.status, LandscapeRunStatus.COMPLETED)
        self.assertEqual(workflow.calls, ["preanalysis", "semantics", "finalize"])
        self.assertFalse(vault.has_credentials("RUN-1"))

    def test_failure_records_only_sanitized_error_type(self):
        async def scenario():
            runs = RunRepository()
            stages = StageRepository()
            stages.stage = SimpleNamespace(
                stage_name="EXTRACT_DIRECTIONS", status=V4StageStatus.RUNNING
            )
            vault = CredentialVault()
            vault.put(
                "RUN-1",
                RuntimeModelConfig(
                    base_url="https://example.test/v1",
                    api_key="secret-value",
                    model="fixture",
                ),
            )
            manager = V4LandscapeTaskManager(
                Workflow(runs, fail=True), runs, stages, vault
            )
            manager.start("RUN-1")
            await manager.tasks["RUN-1"]
            return runs, stages

        runs, stages = asyncio.run(scenario())
        self.assertEqual(runs.value.status, LandscapeRunStatus.FAILED)
        self.assertEqual(runs.error[0], "RuntimeError")
        self.assertNotIn("secret-value", repr((runs.error, stages.failure)))


if __name__ == "__main__":
    unittest.main()
