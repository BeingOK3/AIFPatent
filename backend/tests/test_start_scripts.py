from __future__ import annotations

import unittest
from pathlib import Path


class StartScriptsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.start = Path("start.sh").read_text(encoding="utf-8")
        self.stop = Path("stop.sh").read_text(encoding="utf-8")

    def test_default_start_controls_full_rag_stack_and_checks_disk(self) -> None:
        self.assertIn("docker compose version", self.start)
        self.assertIn("MIN_FREE_KB", self.start)
        self.assertIn("tools/rag_infra.py\" init", self.start)
        self.assertIn("tools/rag_infra.py\" up", self.start)
        self.assertNotIn("tools/rag_infra.py\" migrate", self.start)
        self.assertIn("openapi.json", self.start)

    def test_default_stop_preserves_volumes_and_local_mode_is_explicit(self) -> None:
        self.assertIn('MODE="${1:-docker}"', self.stop)
        self.assertIn("tools/rag_infra.py\" down", self.stop)
        self.assertNotIn("down --volumes", self.stop)
        self.assertNotIn("docker volume rm", self.stop)
        self.assertIn('"--local"', self.start)
        self.assertIn('"--local"', self.stop)

    def test_scripts_never_accept_or_export_model_credentials(self) -> None:
        combined = (self.start + self.stop).upper()
        self.assertNotIn("LLM_API_KEY", combined)
        self.assertNotIn("DEEPSEEK_API_KEY", combined)


if __name__ == "__main__":
    unittest.main()
