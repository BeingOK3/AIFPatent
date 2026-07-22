from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from idea.logging_security import redact_query_credentials


class LoggingSecurityTests(unittest.TestCase):
    def test_redacts_query_key_without_removing_other_log_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app.log"
            path.write_text(
                'GET https://serpapi.com/search.json?q=patent&api_key=secret-value "200"\n'
                "ordinary diagnostic line\n",
                encoding="utf-8",
            )
            self.assertEqual(redact_query_credentials(path), 1)
            content = path.read_text(encoding="utf-8")
            self.assertIn("api_key=[REDACTED]", content)
            self.assertNotIn("secret-value", content)
            self.assertIn("ordinary diagnostic line", content)
            self.assertEqual(redact_query_credentials(path), 0)

    def test_missing_log_is_a_noop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                redact_query_credentials(Path(directory) / "missing.log"), 0
            )


if __name__ == "__main__":
    unittest.main()
