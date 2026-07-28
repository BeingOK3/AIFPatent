from __future__ import annotations

import asyncio
import unittest

from landscape.company_workflow import LandscapeCompanyFanout


class _FanoutHarness:
    def __init__(self):
        self.rows = {}
        self.starts = []

    def latest_task(self, _run_id, step, *, task_key):
        return self.rows.get((step.value, task_key))

    def start_step(self, _run_id, step, _input, *, task_key):
        key = (step.value, task_key)
        attempt = self.rows.get(key, {}).get("attempt", 0) + 1
        self.rows[key] = {"status": "RUNNING", "attempt": attempt}
        self.starts.append((step.value, task_key, attempt))
        return attempt

    def complete_step(
        self, _run_id, step, attempt, _output, *, task_key
    ):
        self.rows[(step.value, task_key)] = {
            "status": "SUCCEEDED",
            "attempt": attempt,
        }

    def fail_step(self, _run_id, step, attempt, _error, *, task_key):
        self.rows[(step.value, task_key)] = {
            "status": "FAILED",
            "attempt": attempt,
        }


class _FanoutExecution:
    def __init__(self, fail_company=None):
        self.calls = []
        self.fail_company = fail_company

    async def analyze_company(self, _run_id, company_id):
        self.calls.append(company_id)
        if company_id == self.fail_company:
            raise ConnectionError("temporary company failure")
        return {"company_id": company_id}


class LandscapeCompanyFanoutTests(unittest.TestCase):
    def test_send_is_stable_and_resume_skips_successful_companies(self):
        harness = _FanoutHarness()
        execution = _FanoutExecution()
        fanout = LandscapeCompanyFanout(
            harness=harness,  # type: ignore[arg-type]
            execution=execution,  # type: ignore[arg-type]
        )

        first = asyncio.run(fanout.execute("run-1", ["CO-B", "CO-A"]))
        second = asyncio.run(fanout.execute("run-1", ["CO-A", "CO-B"]))

        self.assertEqual(first, ["CO-A", "CO-B"])
        self.assertEqual(second, first)
        self.assertEqual(sorted(execution.calls), ["CO-A", "CO-B"])
        self.assertEqual(
            sorted((key, attempt) for _, key, attempt in harness.starts),
            [("CO-A", 1), ("CO-B", 1)],
        )

    def test_failed_branch_preserves_success_and_only_it_retries(self):
        harness = _FanoutHarness()
        failing = _FanoutExecution(fail_company="CO-B")
        fanout = LandscapeCompanyFanout(
            harness=harness,  # type: ignore[arg-type]
            execution=failing,  # type: ignore[arg-type]
        )

        with self.assertRaisesRegex(ConnectionError, "company failure"):
            asyncio.run(fanout.execute("run-1", ["CO-A", "CO-B"]))
        self.assertEqual(
            harness.rows[("ANALYZE_COMPANY", "CO-A")]["status"],
            "SUCCEEDED",
        )
        self.assertEqual(
            harness.rows[("ANALYZE_COMPANY", "CO-B")]["status"],
            "FAILED",
        )

        recovered = _FanoutExecution()
        resumed = LandscapeCompanyFanout(
            harness=harness,  # type: ignore[arg-type]
            execution=recovered,  # type: ignore[arg-type]
        )
        self.assertEqual(
            asyncio.run(resumed.execute("run-1", ["CO-A", "CO-B"])),
            ["CO-A", "CO-B"],
        )
        self.assertEqual(recovered.calls, ["CO-B"])
        self.assertEqual(
            harness.rows[("ANALYZE_COMPANY", "CO-B")]["attempt"],
            2,
        )

    def test_empty_and_duplicate_dispatch_are_explicit(self):
        fanout = LandscapeCompanyFanout(
            harness=_FanoutHarness(),  # type: ignore[arg-type]
            execution=_FanoutExecution(),  # type: ignore[arg-type]
        )
        self.assertEqual(asyncio.run(fanout.execute("run-1", [])), [])
        with self.assertRaisesRegex(ValueError, "unique"):
            asyncio.run(fanout.execute("run-1", ["CO-A", "CO-A"]))


if __name__ == "__main__":
    unittest.main()
