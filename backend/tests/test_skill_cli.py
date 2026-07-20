from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


SCRIPT = Path("tools/idea_workflow.py").resolve()


class FixtureHandler(BaseHTTPRequestHandler):
    calls = []
    health_ok = True

    def log_message(self, *_):
        return

    def _send(self, status, value):
        content = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        self.calls.append(("GET", self.path, None))
        if self.path == "/api/system/health":
            self._send(200, {"ok": self.health_ok, "status": "ok" if self.health_ok else "error"})
        elif self.path == "/api/idea/runs/run-1":
            self._send(200, {
                "run_id": "run-1", "status": "COMPLETED",
                "progress": {"current_step": None, "completed_steps": 11, "total_steps": 11},
            })
        elif self.path == "/api/idea/runs/run-1/report":
            self._send(200, {
                "schema_version": "1.0",
                "conclusion_overview": {"novelty_label": "具备新颖性"},
            })
        elif self.path == "/api/idea/cases":
            self._send(200, {"cases": []})
        else:
            self._send(404, {"detail": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        self.calls.append(("POST", self.path, body))
        if self.path == "/api/idea/cases":
            self._send(200, {"case_id": "case-1", "title": body["title"]})
        elif self.path == "/api/idea/cases/case-1/runs":
            self._send(200, {"case_id": "case-1", "run_id": "run-1", "status": "QUEUED"})
        elif self.path == "/api/idea/runs/run-1/cancel":
            self._send(200, {"run_id": "run-1", "status": "CANCELLED"})
        else:
            self._send(404, {"detail": "not found"})


class SkillWorkflowCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.thread.join(timeout=2)
        cls.server.server_close()

    def setUp(self) -> None:
        FixtureHandler.calls = []
        FixtureHandler.health_ok = True

    def run_cli(self, *arguments):
        environment = os.environ.copy()
        environment["LLM_API_KEY"] = "fixture-runtime-token"
        return subprocess.run(
            [sys.executable, str(SCRIPT), *arguments, "--base-url", self.base_url],
            text=True,
            capture_output=True,
            timeout=5,
            env=environment,
        )

    def test_run_submits_waits_and_returns_only_authoritative_report(self) -> None:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write("A cache controller computes token heat and evicts cold entries.")
            idea_path = handle.name
        try:
            result = self.run_cli(
                "run", "--case-title", "Cache", "--idea-file", idea_path,
                "--model-base-url", "https://runtime.example.test/v1",
                "--model", "runtime-model",
                "--mode", "standard", "--candidate-max", "80", "--deep-min", "10",
                "--deep-max", "20", "--poll", "0.01", "--timeout", "1",
            )
        finally:
            Path(idea_path).unlink(missing_ok=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["conclusion_overview"]["novelty_label"], "具备新颖性")
        paths = [(method, path) for method, path, _ in FixtureHandler.calls]
        self.assertEqual(paths, [
            ("POST", "/api/idea/cases"),
            ("POST", "/api/idea/cases/case-1/runs"),
            ("GET", "/api/idea/runs/run-1"),
            ("GET", "/api/idea/runs/run-1/report"),
        ])
        run_body = FixtureHandler.calls[1][2]
        self.assertEqual(run_body["settings"]["deep_review_min"], 10)
        self.assertEqual(run_body["settings"]["candidate_max"], 80)
        self.assertEqual(run_body["api_key"], "fixture-runtime-token")
        self.assertEqual(run_body["base_url"], "https://runtime.example.test/v1")
        self.assertEqual(run_body["model"], "runtime-model")
        self.assertNotIn("fixture-runtime-token", result.stdout)
        self.assertNotIn("fixture-runtime-token", result.stderr)

    def test_missing_runtime_token_does_not_create_an_orphan_case(self) -> None:
        environment = os.environ.copy()
        environment.pop("LLM_API_KEY", None)
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "start",
                "--idea",
                "long enough technical idea",
                "--model-base-url",
                "https://runtime.example.test/v1",
                "--model",
                "runtime-model",
                "--base-url",
                self.base_url,
            ],
            text=True,
            capture_output=True,
            timeout=5,
            env=environment,
        )
        self.assertEqual(result.returncode, 3)
        self.assertEqual(FixtureHandler.calls, [])

    def test_unhealthy_core_returns_nonzero(self) -> None:
        FixtureHandler.health_ok = False
        result = self.run_cli("health")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(json.loads(result.stdout)["ok"])

    def test_invalid_idea_does_not_create_an_orphan_case(self) -> None:
        result = self.run_cli(
            "start", "--case-title", "Bad", "--idea", "short",
            "--model-base-url", "https://runtime.example.test/v1",
            "--model", "runtime-model",
        )
        self.assertEqual(result.returncode, 3)
        self.assertEqual(FixtureHandler.calls, [])

    def test_invalid_budget_does_not_create_an_orphan_case(self) -> None:
        result = self.run_cli(
            "start", "--idea", "long enough technical idea", "--deep-min", "9",
            "--model-base-url", "https://runtime.example.test/v1",
            "--model", "runtime-model",
        )
        self.assertEqual(result.returncode, 3)
        self.assertEqual(FixtureHandler.calls, [])

    def test_cli_is_independent_from_opencode(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("opencode", source.lower())
        self.assertIn("/api/idea/cases", source)


if __name__ == "__main__":
    unittest.main()
