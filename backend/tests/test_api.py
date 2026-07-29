from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from idea.api import RunTaskManager, create_idea_router
from idea.config import load_config
from idea.database import Database, now_ms
from idea.model_client import RuntimeModelConfig
from idea.run_store import RunStore
from idea.workflow import WorkflowHarness


class RecordingManager:
    def __init__(self, database, harness):
        self.database = database
        self.harness = harness
        self.started = []

    def start(self, run_id, *, runtime_config=None):
        self.started.append(
            (run_id, runtime_config.base_url, runtime_config.model, bool(runtime_config.api_key))
        )
        return True

    async def cancel(self, run_id):
        run = self.database.get_run(run_id)
        if run["status"] in {"COMPLETED", "COMPLETED_WITH_LIMITATIONS", "FAILED", "CANCELLED"}:
            return False
        self.harness.cancel_run(run_id)
        return True


class WaitingExecutor:
    def __init__(self):
        self.started = asyncio.Event()

    async def execute(self, run_id):
        self.started.set()
        await asyncio.Event().wait()


class IdeaApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        config = load_config()
        storage = config.storage.model_copy(
            update={
                "database": root / "idea.db",
                "runs_dir": root / "runs",
                "uploads_dir": root / "uploads",
                "cache_dir": root / "cache",
            }
        )
        self.config = config.model_copy(update={"storage": storage})
        self.config.storage.uploads_dir.mkdir(parents=True)
        self.db = Database(root / "idea.db")
        self.db.initialize()
        self.store = RunStore(self.config.storage.runs_dir)
        self.harness = WorkflowHarness(self.db, self.store, max_step_attempts=2)
        self.manager = RecordingManager(self.db, self.harness)
        app = FastAPI()
        app.include_router(
            create_idea_router(
                self.config, self.db, self.store, self.harness, self.manager
            )
        )
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.temp.cleanup()

    def create_case(self):
        response = self.client.post("/api/idea/cases", json={"title": "Internal idea"})
        self.assertEqual(response.status_code, 200)
        return response.json()

    def create_run(self, case_id, **overrides):
        payload = {
            "api_key": "test-runtime-token",
            "base_url": "https://runtime.example.test/v1",
            "model": "runtime-model",
            "input_text": "A cache controller computes token heat and evicts cold entries.",
            "evaluation_date": "2026-07-16",
            "settings": {"search_mode": "quick"},
            **overrides,
        }
        return self.client.post(f"/api/idea/cases/{case_id}/runs", json=payload)

    def test_case_run_history_rerun_and_manual_delete(self) -> None:
        case = self.create_case()
        created = self.create_run(case["case_id"])
        self.assertEqual(created.status_code, 200)
        run = created.json()
        self.assertEqual(run["status"], "QUEUED")
        self.assertEqual(
            self.manager.started,
            [(run["run_id"], "https://runtime.example.test/v1", "runtime-model", True)],
        )
        self.assertEqual(run["model"], "runtime-model")
        self.assertEqual(run["base_url"], "https://runtime.example.test/v1")

        detail = self.client.get(f"/api/idea/runs/{run['run_id']}").json()
        self.assertEqual(detail["progress"]["total_steps"], 11)
        self.assertEqual(
            detail["input_text"],
            "A cache controller computes token heat and evicts cold entries.",
        )
        self.assertEqual(detail["date_basis"], "用户指定或提交日")
        self.assertEqual(len(detail["input_hash"]), 64)
        rerun = self.client.post(
            f"/api/idea/runs/{run['run_id']}/rerun",
            json={
                "api_key": "test-rerun-token",
                "base_url": "https://rerun.example.test/v1",
                "model": "rerun-model",
            },
        ).json()
        self.assertEqual(rerun["parent_run_id"], run["run_id"])
        self.assertNotEqual(rerun["run_id"], run["run_id"])
        self.assertEqual(rerun["model"], "rerun-model")
        history = self.client.get(f"/api/idea/cases/{case['case_id']}").json()
        self.assertEqual(len(history["runs"]), 2)
        self.assertIn("cache controller", history["runs"][0]["input_preview"])
        self.assertEqual(
            self.db.get_run(run["run_id"])["input_text"], detail["input_text"]
        )

        deleted = self.client.request(
            "DELETE", f"/api/idea/runs/{rerun['run_id']}", json={"operator_label": "tester"}
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(
            self.client.get(f"/api/idea/runs/{rerun['run_id']}").status_code, 404
        )

    def test_duplicate_case_titles_are_rejected_as_conflicts(self) -> None:
        first = self.client.post("/api/idea/cases", json={"title": "OCR 结构识别"})
        duplicate = self.client.post(
            "/api/idea/cases", json={"title": "  OCR 结构识别  "}
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(duplicate.status_code, 409)
        self.assertIn("名称已存在", duplicate.json()["detail"])

    def test_invalid_custom_limits_fail_before_run_creation(self) -> None:
        case = self.create_case()
        response = self.create_run(
            case["case_id"],
            settings={"search_mode": "quick", "deep_review_min": 30},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.db.get_case(case["case_id"])["runs"], [])

    def test_attachment_names_are_scoped_to_upload_directory(self) -> None:
        case = self.create_case()
        (self.config.storage.uploads_dir / "reference.txt").write_text(
            "public reference", encoding="utf-8"
        )
        valid = self.create_run(case["case_id"], attachment_names=["reference.txt"])
        self.assertEqual(valid.status_code, 200)
        invalid = self.create_run(case["case_id"], attachment_names=["../secret.txt"])
        self.assertEqual(invalid.status_code, 422)

    def test_missing_case_and_report_are_explicit_404(self) -> None:
        self.assertEqual(self.client.get("/api/idea/cases/missing").status_code, 404)
        case = self.create_case()
        run = self.create_run(case["case_id"]).json()
        self.assertEqual(
            self.client.get(f"/api/idea/runs/{run['run_id']}/report").status_code,
            404,
        )

    def test_runtime_api_key_is_required_and_never_persisted(self) -> None:
        case = self.create_case()
        missing = self.create_run(case["case_id"], api_key=None)
        blank = self.create_run(case["case_id"], api_key="   ")
        self.assertEqual(missing.status_code, 422)
        self.assertEqual(blank.status_code, 422)
        self.assertEqual(self.db.get_case(case["case_id"])["runs"], [])

        secret = "fixture-ephemeral-token-123"
        created = self.create_run(case["case_id"], api_key=secret)
        self.assertEqual(created.status_code, 200)
        run_id = created.json()["run_id"]
        persisted = json.dumps(self.db.get_run(run_id), ensure_ascii=False, default=str)
        response = json.dumps(created.json(), ensure_ascii=False)
        self.assertNotIn(secret, persisted)
        self.assertNotIn(secret, response)
        self.assertEqual(self.db.get_run(run_id)["model"], "runtime-model")

    def test_runtime_model_config_rejects_unsafe_or_missing_values(self) -> None:
        case = self.create_case()
        for overrides in (
            {"base_url": None},
            {"model": "   "},
            {"base_url": "https://user:password@example.test/v1"},
            {"base_url": "https://example.test/v1?credential=value"},
        ):
            response = self.create_run(case["case_id"], **overrides)
            self.assertEqual(response.status_code, 422)
        self.assertEqual(self.db.get_case(case["case_id"])["runs"], [])

    def test_debug_endpoint_exposes_sanitized_trace_contract(self) -> None:
        case = self.create_case()
        run = self.create_run(case["case_id"]).json()
        response = self.client.get(f"/api/idea/runs/{run['run_id']}/debug")
        self.assertEqual(response.status_code, 200)
        trace = response.json()
        self.assertEqual(trace["run"]["run_id"], run["run_id"])
        self.assertEqual(trace["tool_calls"], [])
        self.assertTrue(trace["log_storage"]["git_ignored"])

    def test_terminal_sse_emits_persisted_progress_and_terminal_event(self) -> None:
        case = self.create_case()
        run = self.create_run(case["case_id"]).json()
        self.harness.cancel_run(run["run_id"])
        response = self.client.get(f"/api/idea/runs/{run['run_id']}/events")
        self.assertEqual(response.status_code, 200)
        self.assertIn('"type": "progress"', response.text)
        self.assertIn('"type": "terminal"', response.text)
        self.assertIn('"status": "CANCELLED"', response.text)

    def test_report_endpoint_verifies_manifest_before_returning_json(self) -> None:
        case = self.create_case()
        created = self.create_run(case["case_id"]).json()
        run = self.db.get_run(created["run_id"])
        paths = self.store.initialize_run(case["case_id"], run["run_id"])
        self.store.snapshot_input(
            case["case_id"], run["run_id"], input_text=run["input_text"], metadata={}
        )
        manifest = self.store.write_reports(
            case["case_id"], run["run_id"], report={"result": "ok"},
            markdown="# report\n", manifest_metadata={},
        )
        with self.db.connect() as connection:
            connection.execute(
                "INSERT INTO reports VALUES(?,?,?,?,?,?,?)",
                (
                    run["run_id"], str(paths.report_json),
                    manifest["files"]["report.json"]["sha256"], str(paths.report_md),
                    manifest["files"]["report.md"]["sha256"], str(paths.manifest), now_ms(),
                ),
            )
        response = self.client.get(f"/api/idea/runs/{run['run_id']}/report")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"result": "ok"})
        paths.report_json.write_text('{"result":"tampered"}', encoding="utf-8")
        self.assertEqual(
            self.client.get(f"/api/idea/runs/{run['run_id']}/report").status_code,
            409,
        )


class RunTaskManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.db = Database(root / "idea.db")
        self.db.initialize()
        self.store = RunStore(root / "runs")
        self.harness = WorkflowHarness(self.db, self.store, max_step_attempts=2)
        case = self.db.create_case("tasks")
        self.run = self.db.create_run(
            case_id=case["case_id"], input_text="long enough idea", evaluation_date="2026-07-16",
            date_basis="default", analysis_scope="full", model="stub", skill_version="1",
            workflow_version="1", config_snapshot={},
        )
        self.executor = WaitingExecutor()
        self.manager = RunTaskManager(self.db, self.harness, self.executor)

    async def asyncTearDown(self) -> None:
        for run_id in list(self.manager.tasks):
            await self.manager.cancel(run_id)
        self.temp.cleanup()

    async def test_start_is_deduplicated_and_cancel_reaches_terminal_state(self) -> None:
        runtime = RuntimeModelConfig("https://runtime.test", "runtime-only", "fixture-model")
        self.assertTrue(self.manager.start(self.run["run_id"], runtime_config=runtime))
        self.assertFalse(self.manager.start(self.run["run_id"], runtime_config=runtime))
        await self.executor.started.wait()
        self.assertIn(self.run["run_id"], self.manager._runtime_configs)
        self.assertTrue(await self.manager.cancel(self.run["run_id"]))
        self.assertEqual(self.db.get_run(self.run["run_id"])["status"], "CANCELLED")
        self.assertNotIn(self.run["run_id"], self.manager._runtime_configs)

    async def test_restart_fails_incomplete_run_without_persisted_key(self) -> None:
        interrupted = self.manager.resume_incomplete()
        self.assertEqual(interrupted, [self.run["run_id"]])
        run = self.db.get_run(self.run["run_id"])
        self.assertEqual(run["status"], "FAILED")
        self.assertEqual(run["error_code"], "RUNTIME_API_KEY_REQUIRED_AFTER_RESTART")
        self.assertFalse(self.executor.started.is_set())


if __name__ == "__main__":
    unittest.main()
