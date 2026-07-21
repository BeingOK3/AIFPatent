from __future__ import annotations

import ast
import unittest
from pathlib import Path


TOOL = Path("tools/sync_postgres_run_status.py")


class SyncPostgreSQLRunStatusToolTests(unittest.TestCase):
    def test_tool_defaults_to_terminal_runs_and_prints_counts_only(self) -> None:
        source = TOOL.read_text(encoding="utf-8")
        ast.parse(source)

        self.assertIn("COMPLETED_WITH_LIMITATIONS", source)
        self.assertIn("include_nonterminal", source)
        self.assertIn("source_runs=", source)
        self.assertIn("synchronized=", source)
        self.assertNotIn("api_key", source.lower())
        self.assertNotIn("input_text", source)


if __name__ == "__main__":
    unittest.main()
