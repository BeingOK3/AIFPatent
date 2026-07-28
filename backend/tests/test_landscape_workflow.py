from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from landscape.database import LandscapeDatabase
from landscape.schemas import AnalysisMode, LandscapeScope
from landscape.store import LandscapeRunStore
from landscape.workflow import (
    LandscapeWorkflowHarness,
    LandscapeWorkflowStep,
    NonRetryableLandscapeWorkflowError,
    _retry_transient_error,
)


class LandscapeWorkflowHarnessTests(unittest.TestCase):
    def test_deterministic_policy_gate_is_not_retried(self) -> None:
        self.assertFalse(
            _retry_transient_error(NonRetryableLandscapeWorkflowError("policy gate"))
        )
        self.assertTrue(_retry_transient_error(ConnectionError("temporary")))

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.db = LandscapeDatabase(root / "landscape.db")
        self.db.initialize()
        self.store = LandscapeRunStore(root / "runs")
        self.scope = LandscapeScope(
            mode=AnalysisMode.TECHNOLOGY,
            technology_direction="液冷",
            publication_start=date(2026, 4, 1),
            publication_end=date(2026, 6, 30),
        )
        self.run = self.db.create_run(
            scope=self.scope,
            model="deepseek-v4-flash",
            workflow_version="1.0.0",
            prompt_version="1.0.0",
        )
        self.store.initialize_run(
            self.run["run_id"], scope=self.scope, model="deepseek-v4-flash",
            workflow_version="1.0.0", prompt_version="1.0.0",
        )
        self.harness = LandscapeWorkflowHarness(self.db, self.store, max_step_attempts=2)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_steps_are_ordered_and_report_manifest_gates_completion(self) -> None:
        run_id = self.run["run_id"]
        self.harness.begin_run(run_id)
        with self.assertRaisesRegex(Exception, "out-of-order"):
            self.harness.start_step(run_id, LandscapeWorkflowStep.PLAN_SEARCH, {})
        for step in LandscapeWorkflowStep:
            attempt = self.harness.start_step(run_id, step, {"step": step.value})
            self.harness.complete_step(run_id, step, attempt, {"ok": True})
        self.assertTrue(self.harness.completion_issues(run_id))
        self.store.write_reports(
            run_id,
            report={"summary": {}},
            markdown="# report\n",
            patents_csv="publication_number\n",
            manifest_metadata={},
        )
        self.assertEqual(self.harness.finish_run(run_id, []), "COMPLETED")

    def test_cancelled_run_is_terminal(self) -> None:
        run_id = self.run["run_id"]
        self.harness.begin_run(run_id)
        self.harness.cancel_run(run_id)
        self.assertEqual(self.db.get_run(run_id)["status"], "CANCELLED")
        with self.assertRaisesRegex(Exception, "not RUNNING"):
            self.harness.start_step(run_id, LandscapeWorkflowStep.VALIDATE_SCOPE, {})

    def test_keyed_attempts_are_isolated_and_resumable(self) -> None:
        run_id = self.run["run_id"]
        step = LandscapeWorkflowStep.ANALYZE_PATENTS
        self.harness.begin_run(run_id)

        company_a_attempt = self.harness.start_step(
            run_id, step, {"company_id": "CO-A"}, task_key="CO-A"
        )
        company_b_attempt = self.harness.start_step(
            run_id, step, {"company_id": "CO-B"}, task_key="CO-B"
        )
        self.assertEqual((company_a_attempt, company_b_attempt), (1, 1))

        self.harness.complete_step(
            run_id,
            step,
            company_a_attempt,
            {"company_id": "CO-A"},
            task_key="CO-A",
        )
        self.harness.fail_step(
            run_id,
            step,
            company_b_attempt,
            ConnectionError("temporary"),
            task_key="CO-B",
        )
        company_b_retry = self.harness.start_step(
            run_id, step, {"company_id": "CO-B"}, task_key="CO-B"
        )
        self.assertEqual(company_b_retry, 2)
        self.harness.fail_step(
            run_id,
            step,
            company_b_retry,
            ConnectionError("still temporary"),
            task_key="CO-B",
        )

        self.assertEqual(
            self.harness.latest_task(run_id, step, task_key="CO-A")["status"],
            "SUCCEEDED",
        )
        self.assertEqual(
            self.harness.latest_task(run_id, step, task_key="CO-B")["attempt"],
            2,
        )
        self.assertEqual(self.db.get_run(run_id)["status"], "RUNNING")
        self.assertEqual(
            self.harness.next_step(run_id),
            LandscapeWorkflowStep.VALIDATE_SCOPE,
        )
        with self.assertRaises(KeyError):
            self.db.get_stage_result(run_id, step.value)

    def test_task_key_is_validated_and_completion_is_task_scoped(self) -> None:
        run_id = self.run["run_id"]
        step = LandscapeWorkflowStep.ANALYZE_PATENTS
        self.harness.begin_run(run_id)
        with self.assertRaisesRegex(Exception, "trimmed"):
            self.harness.start_step(run_id, step, {}, task_key=" CO-A")

        attempt = self.harness.start_step(
            run_id, step, {}, task_key="CO-A"
        )
        with self.assertRaisesRegex(Exception, "not found"):
            self.harness.complete_step(
                run_id, step, attempt, {}, task_key="CO-B"
            )
        self.assertEqual(
            self.harness.latest_task(run_id, step, task_key="CO-A")["status"],
            "RUNNING",
        )


if __name__ == "__main__":
    unittest.main()
