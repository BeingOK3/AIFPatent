from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from idea.run_store import RunStore, RunStoreError


class RunStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = RunStore(self.root / "runs")
        self.case_id = "case-001"
        self.run_id = "run-001"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def complete_run(self):
        self.store.initialize_run(self.case_id, self.run_id)
        attachment = self.root / "draft.txt"
        attachment.write_text("权利要求草稿", encoding="utf-8")
        self.store.snapshot_input(
            self.case_id,
            self.run_id,
            input_text="一种新的缓存方法",
            metadata={"mode": "standard"},
            attachments=[attachment],
        )
        return self.store.write_reports(
            self.case_id,
            self.run_id,
            report={"novelty": {"conclusion": "NOVEL"}},
            markdown="# 报告\n\n具备新颖性。",
            manifest_metadata={"workflow_version": "1.0.0"},
        )

    def test_complete_run_is_verifiable(self) -> None:
        manifest = self.complete_run()
        verified = self.store.verify(self.case_id, self.run_id)
        self.assertEqual(verified, manifest)
        self.assertIn("input/idea.txt", manifest["files"])
        self.assertIn("input/draft.txt", manifest["files"])
        self.assertIn("report.json", manifest["files"])
        self.assertIn("report.md", manifest["files"])

    def test_corruption_is_detected(self) -> None:
        self.complete_run()
        paths = self.store.paths(self.case_id, self.run_id)
        paths.report_md.write_text("已被篡改", encoding="utf-8")
        with self.assertRaisesRegex(RunStoreError, "mismatch"):
            self.store.verify(self.case_id, self.run_id)

    def test_manifest_requires_input_snapshot(self) -> None:
        self.store.initialize_run(self.case_id, self.run_id)
        with self.assertRaisesRegex(RunStoreError, "input snapshot"):
            self.store.write_reports(
                self.case_id,
                self.run_id,
                report={},
                markdown="",
                manifest_metadata={},
            )

    def test_unsafe_identifiers_are_rejected(self) -> None:
        with self.assertRaises(RunStoreError):
            self.store.paths("../other", self.run_id)
        with self.assertRaises(RunStoreError):
            self.store.paths(self.case_id, "/tmp/run")

    def test_manual_delete_does_not_touch_sibling_run(self) -> None:
        self.complete_run()
        sibling = "run-002"
        self.store.initialize_run(self.case_id, sibling)
        self.store.snapshot_input(
            self.case_id,
            sibling,
            input_text="另一个版本",
            metadata={},
        )
        self.store.delete_run(self.case_id, self.run_id)
        self.assertFalse(self.store.paths(self.case_id, self.run_id).root.exists())
        self.assertTrue(self.store.paths(self.case_id, sibling).root.exists())

    def test_duplicate_attachment_names_are_rejected(self) -> None:
        self.store.initialize_run(self.case_id, self.run_id)
        first_dir = self.root / "a"
        second_dir = self.root / "b"
        first_dir.mkdir()
        second_dir.mkdir()
        first = first_dir / "same.txt"
        second = second_dir / "same.txt"
        first.write_text("a", encoding="utf-8")
        second.write_text("b", encoding="utf-8")
        with self.assertRaisesRegex(RunStoreError, "duplicate"):
            self.store.snapshot_input(
                self.case_id,
                self.run_id,
                input_text="idea",
                metadata={},
                attachments=[first, second],
            )


if __name__ == "__main__":
    unittest.main()
