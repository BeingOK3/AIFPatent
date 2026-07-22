from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from landscape.schemas import AnalysisMode, LandscapeScope
from landscape.store import LandscapeRunStore, LandscapeStoreError


class LandscapeRunStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = LandscapeRunStore(self.root / "landscape-runs")
        self.run_id = "landscape-001"
        self.scope = LandscapeScope(
            mode=AnalysisMode.TECHNOLOGY,
            technology_direction="数据中心液冷",
            publication_start=date(2026, 4, 1),
            publication_end=date(2026, 7, 1),
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def initialize(self) -> None:
        self.store.initialize_run(
            self.run_id,
            scope=self.scope,
            model="deepseek-v4-flash",
            workflow_version="1.0.0",
            prompt_version="1.0.0",
        )

    def complete(self):
        self.initialize()
        return self.store.write_reports(
            self.run_id,
            report={"summary": {"candidate_count": 1}},
            markdown="# 专利态势分析\n",
            patents_csv="publication_number,title\nCN123,A\n",
            manifest_metadata={"status": "COMPLETED"},
        )

    def test_report_bundle_is_complete_and_verifiable(self) -> None:
        manifest = self.complete()
        self.assertEqual(self.store.verify(self.run_id), manifest)
        self.assertEqual(set(manifest["files"]), {"input.json", "report.json", "report.md", "patents.csv"})

    def test_manifest_is_completion_marker(self) -> None:
        self.initialize()
        paths = self.store.paths(self.run_id)
        self.assertFalse(paths.manifest.exists())
        with self.assertRaisesRegex(LandscapeStoreError, "manifest"):
            self.store.verify(self.run_id)

    def test_corruption_is_detected(self) -> None:
        self.complete()
        self.store.paths(self.run_id).patents_csv.write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(LandscapeStoreError, "mismatch"):
            self.store.verify(self.run_id)

    def test_credentials_are_rejected_from_report_files(self) -> None:
        self.initialize()
        with self.assertRaisesRegex(ValueError, "sensitive field"):
            self.store.write_reports(
                self.run_id,
                report={"api_key": "credential-must-never-persist"},
                markdown="",
                patents_csv="",
                manifest_metadata={},
            )

    def test_unsafe_run_id_is_rejected(self) -> None:
        with self.assertRaises(LandscapeStoreError):
            self.store.paths("../idea-run")


if __name__ == "__main__":
    unittest.main()
