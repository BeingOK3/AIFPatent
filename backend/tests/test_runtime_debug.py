from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from idea.runtime_debug import RunDebugLog


class RunDebugLogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.log = RunDebugLog(Path(self.temp.name) / "workspace" / "debug" / "idea-runs")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_jsonl_is_append_only_and_sensitive_fields_are_redacted(self) -> None:
        run_id = "run-1"
        self.log.append(
            run_id,
            "tool_call_started",
            provider="fixture",
            api_key="must-not-appear",
            authorization="must-not-appear",
            usage={"prompt_tokens": 12, "completion_tokens": 4},
        )
        self.log.append(run_id, "tool_call_completed", duration_ms=20)
        records = self.log.read(run_id)
        self.assertEqual([item["event"] for item in records], [
            "tool_call_started",
            "tool_call_completed",
        ])
        rendered = json.dumps(records)
        self.assertNotIn("must-not-appear", rendered)
        self.assertEqual(records[0]["details"]["api_key"], "[REDACTED]")
        self.assertEqual(records[0]["details"]["usage"]["prompt_tokens"], 12)

    def test_run_id_cannot_escape_debug_directory(self) -> None:
        path = self.log.path("../../outside")
        self.assertEqual(path.parent, self.log.root)
        self.assertNotIn("..", path.name)


if __name__ == "__main__":
    unittest.main()
