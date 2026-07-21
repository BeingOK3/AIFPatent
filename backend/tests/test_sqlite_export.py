from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from idea.database import Database
from idea.sqlite_export import SQLiteExportError, export_database, write_export


class SQLiteExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database_path = self.root / "aifpatent.db"
        self.database = Database(self.database_path)
        self.database.initialize()
        case = self.database.create_case("Export fixture")
        self.database.create_run(
            case_id=case["case_id"],
            input_text="A deterministic migration fixture.",
            evaluation_date="2026-07-21",
            date_basis="fixture",
            analysis_scope="quick",
            model="fixture-model",
            skill_version="test",
            workflow_version="test",
            config_snapshot={"features": {"patent_corpus": False}},
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_export_is_deterministic_and_contains_integrity_metadata(self) -> None:
        first = export_database(self.database_path)
        second = export_database(self.database_path)
        self.assertEqual(first, second)
        self.assertEqual(first["export_version"], "sqlite-export/1")
        self.assertEqual(first["source"]["user_version"], 3)
        self.assertGreaterEqual(sum(table["row_count"] for table in first["tables"]), 2)
        without_hash = dict(first)
        without_hash.pop("export_hash")
        expected = hashlib.sha256(
            json.dumps(without_hash, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            .encode("utf-8")
        ).hexdigest()
        self.assertEqual(first["export_hash"], expected)

    def test_export_does_not_open_database_for_writing(self) -> None:
        before = self.database_path.stat().st_mtime_ns
        export_database(self.database_path)
        self.assertEqual(self.database_path.stat().st_mtime_ns, before)

    def test_write_export_is_private_atomic_and_does_not_overwrite(self) -> None:
        output = self.root / "migration" / "export.json"
        payload = export_database(self.database_path)
        self.assertEqual(write_export(payload, output), output.resolve())
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(SQLiteExportError):
            write_export(payload, output)
        self.assertEqual(list(output.parent.glob(".sqlite-export-*")), [])

    def test_missing_database_fails_closed(self) -> None:
        with self.assertRaises(SQLiteExportError):
            export_database(self.root / "missing.db")


if __name__ == "__main__":
    unittest.main()
