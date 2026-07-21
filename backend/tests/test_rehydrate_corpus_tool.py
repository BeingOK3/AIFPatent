from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from idea.corpus_migration import MigrationOutcome, MigrationReport, MigrationStatus


TOOL_PATH = Path(__file__).resolve().parents[2] / "tools" / "rehydrate_corpus.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("rehydrate_corpus_tool", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RehydrateCorpusToolTests(unittest.TestCase):
    def test_parser_is_dry_run_by_default(self) -> None:
        tool = load_tool()

        args = tool.build_parser().parse_args([])

        self.assertFalse(args.apply)
        self.assertIsNone(args.run_id)
        self.assertIsNone(args.limit)

    def test_dry_run_builder_does_not_construct_external_adapters(self) -> None:
        tool = load_tool()
        config = tool.load_config()
        with patch.object(
            tool, "build_corpus_ingest", side_effect=AssertionError("external corpus")
        ), patch.object(
            tool, "_providers", side_effect=AssertionError("external providers")
        ):
            migrator = tool.build_migrator(config, apply=False)

        self.assertIsNone(migrator.ingest)
        self.assertIsNone(migrator.fetcher)

    def test_report_payload_contains_outcomes_but_no_document_text(self) -> None:
        tool = load_tool()
        report = MigrationReport(
            apply=True,
            outcomes=(
                MigrationOutcome(
                    run_id="run-1",
                    document_id="doc-1",
                    publication_number="CN100",
                    status=MigrationStatus.REHYDRATED,
                    version_id="cv-1",
                ),
            ),
        )

        payload = tool.report_payload(report)

        self.assertEqual(payload["mode"], "apply")
        self.assertEqual(payload["counts"], {"REHYDRATED": 1})
        self.assertEqual(payload["outcomes"][0]["version_id"], "cv-1")
        self.assertNotIn("text", str(payload).lower())

    def test_write_report_is_atomic_and_private(self) -> None:
        tool = load_tool()
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "nested" / "report.json"

            tool.write_report(destination, {"mode": "dry-run", "outcomes": []})

            self.assertTrue(destination.is_file())
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)

    def test_error_message_redacts_configured_credentials(self) -> None:
        tool = load_tool()
        with patch.dict(
            os.environ,
            {"AIFPATENT_S3_SECRET_KEY": "do-not-print-this-secret"},
            clear=False,
        ):
            message = tool.safe_error_message(
                RuntimeError("failed using do-not-print-this-secret")
            )

        self.assertNotIn("do-not-print-this-secret", message)
        self.assertIn("<redacted>", message)


if __name__ == "__main__":
    unittest.main()
