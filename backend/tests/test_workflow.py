from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from idea.database import Database
from idea.run_store import RunStore
from idea.workflow import (
    CompletionGateError,
    WORKFLOW_STEPS,
    WorkflowError,
    WorkflowHarness,
)


class WorkflowHarnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.database = Database(root / "idea.db")
        self.database.initialize()
        self.store = RunStore(root / "runs")
        self.workflow = WorkflowHarness(self.database, self.store, max_step_attempts=2)
        case = self.database.create_case("Workflow test")
        self.run = self.database.create_run(
            case_id=case["case_id"],
            input_text="一种缓存方法",
            evaluation_date="2026-07-16",
            date_basis="default",
            analysis_scope="full",
            model="deepseek-v4-flash",
            skill_version="1",
            workflow_version="1",
            config_snapshot={},
        )
        self.paths = self.store.initialize_run(case["case_id"], self.run["run_id"])
        self.store.snapshot_input(
            case["case_id"], self.run["run_id"], input_text="一种缓存方法", metadata={}
        )
        self.workflow.begin_run(self.run["run_id"])

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_steps_must_run_in_order(self) -> None:
        with self.assertRaisesRegex(WorkflowError, "out-of-order"):
            self.workflow.start_step(self.run["run_id"], WORKFLOW_STEPS[1], {})
        attempt = self.workflow.start_step(self.run["run_id"], WORKFLOW_STEPS[0], {})
        self.workflow.complete_step(self.run["run_id"], WORKFLOW_STEPS[0], attempt, {"ok": True})
        self.assertEqual(self.workflow.next_step(self.run["run_id"]), WORKFLOW_STEPS[1])

    def test_failed_step_can_retry_and_attempt_limit_fails_run(self) -> None:
        step = WORKFLOW_STEPS[0]
        first = self.workflow.start_step(self.run["run_id"], step, {})
        self.workflow.fail_step(
            self.run["run_id"], step, first, error_code="TEMP", error_message="temporary"
        )
        second = self.workflow.start_step(self.run["run_id"], step, {})
        self.workflow.fail_step(
            self.run["run_id"], step, second, error_code="FATAL", error_message="failed"
        )
        self.assertEqual(self.database.get_run(self.run["run_id"])["status"], "FAILED")

    def test_recovery_interrupts_active_step_and_allows_retry(self) -> None:
        step = WORKFLOW_STEPS[0]
        self.workflow.start_step(self.run["run_id"], step, {})
        recovered = self.workflow.recover_incomplete()
        self.assertEqual(recovered, [self.run["run_id"]])
        self.assertTrue(self.workflow.recovery_ready)
        retry = self.workflow.start_step(self.run["run_id"], step, {})
        self.assertEqual(retry, 2)

    def test_completion_requires_all_steps_manifest_and_clean_audit(self) -> None:
        for step in WORKFLOW_STEPS:
            attempt = self.workflow.start_step(self.run["run_id"], step, {"step": step.value})
            self.workflow.complete_step(
                self.run["run_id"], step, attempt, {"step": step.value, "ok": True}
            )
        with self.assertRaises(CompletionGateError):
            self.workflow.finish_run(self.run["run_id"], limitations=[])
        self.store.write_reports(
            self.run["case_id"],
            self.run["run_id"],
            report={"conclusion": "NOVEL"},
            markdown="# Report",
            manifest_metadata={},
        )
        status = self.workflow.finish_run(self.run["run_id"], limitations=[])
        self.assertEqual(status, "COMPLETED")

    def test_critical_audit_blocks_completion(self) -> None:
        for step in WORKFLOW_STEPS:
            attempt = self.workflow.start_step(self.run["run_id"], step, {})
            self.workflow.complete_step(self.run["run_id"], step, attempt, {})
        self.store.write_reports(
            self.run["case_id"], self.run["run_id"], report={}, markdown="# Report", manifest_metadata={}
        )
        with self.database.connect() as connection:
            connection.execute(
                "INSERT INTO audit_results VALUES(?,?,?,?,?,?,?)",
                ("A1", self.run["run_id"], "critical", "BAD_EVIDENCE", "bad", "{}", 1),
            )
        with self.assertRaisesRegex(CompletionGateError, "critical"):
            self.workflow.finish_run(self.run["run_id"], limitations=[])

    def test_cancel_is_terminal(self) -> None:
        self.workflow.cancel_run(self.run["run_id"])
        with self.assertRaisesRegex(WorkflowError, "illegal"):
            self.workflow.begin_run(self.run["run_id"])

    def test_progress_comes_from_persisted_steps(self) -> None:
        step = WORKFLOW_STEPS[0]
        attempt = self.workflow.start_step(self.run["run_id"], step, {})
        self.workflow.complete_step(self.run["run_id"], step, attempt, {})
        progress = self.workflow.progress(self.run["run_id"])
        self.assertEqual(progress["completed_steps"], 1)
        self.assertEqual(progress["current_step"], WORKFLOW_STEPS[1].value)
        self.assertEqual(progress["steps"][0]["status"], "SUCCEEDED")


if __name__ == "__main__":
    unittest.main()
