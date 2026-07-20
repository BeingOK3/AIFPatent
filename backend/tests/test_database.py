from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from idea.database import Database


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "idea.db")
        self.db.initialize()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def create_run(self, case_id: str, text: str = "一种缓存调度方法", parent: str | None = None):
        return self.db.create_run(
            case_id=case_id,
            input_text=text,
            evaluation_date="2026-07-16",
            date_basis="用户指定",
            analysis_scope="full",
            model="deepseek-v4-flash",
            skill_version="1.0.0",
            workflow_version="1.0.0",
            config_snapshot={"cache": {"max_bytes": 1024**3}},
            settings={"mode": "standard"},
            parent_run_id=parent,
        )

    def test_migration_creates_all_core_tables(self) -> None:
        expected = {
            "idea_cases", "idea_runs", "run_inputs", "run_steps", "tool_calls",
            "search_queries", "search_hits", "patent_documents", "patent_families",
            "run_documents", "idea_features", "evidence", "feature_mappings",
            "novelty_results", "inventive_routes", "value_results", "audit_results",
            "reports", "artifacts", "cache_entries", "deletion_events", "stage_results",
            "provider_circuit_breakers",
        }
        self.assertTrue(expected.issubset(self.db.table_names()))
        with self.db.connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(version, 3)

    def test_stage_results_are_hashed_write_once_checkpoints(self) -> None:
        case = self.db.create_case("checkpoint")
        run = self.create_run(case["case_id"])
        first = self.db.put_stage_result(run["run_id"], "PARSE_IDEA", {"features": ["F1"]})
        second = self.db.put_stage_result(run["run_id"], "PARSE_IDEA", {"features": ["F1"]})
        self.assertEqual(first["content_hash"], second["content_hash"])
        self.assertEqual(
            self.db.get_stage_result(run["run_id"], "PARSE_IDEA")["value"],
            {"features": ["F1"]},
        )
        with self.assertRaisesRegex(ValueError, "immutable"):
            self.db.put_stage_result(run["run_id"], "PARSE_IDEA", {"features": ["F2"]})

    def test_rerun_creates_new_immutable_history(self) -> None:
        case = self.db.create_case("KV Cache 调度")
        first = self.create_run(case["case_id"], "第一版技术方案")
        second = self.create_run(case["case_id"], "第二版技术方案", first["run_id"])

        self.assertNotEqual(first["run_id"], second["run_id"])
        self.assertEqual(second["parent_run_id"], first["run_id"])
        self.assertEqual(self.db.get_run(first["run_id"])["input_text"], "第一版技术方案")
        history = self.db.get_case(case["case_id"])["runs"]
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["input_preview"], "第二版技术方案")
        self.assertEqual(history[1]["input_hash"], first["input_hash"])

    def test_case_titles_are_unique_after_trimming_and_case_insensitive(self) -> None:
        self.db.create_case("OCR 结构识别")
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.db.create_case("  OCR 结构识别  ")
        self.db.create_case("Cache Control")
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.db.create_case("cache control")

    def test_input_and_configuration_cannot_be_updated(self) -> None:
        case = self.db.create_case("不可变测试")
        run = self.create_run(case["case_id"])
        with self.assertRaises(sqlite3.IntegrityError):
            with self.db.connect() as connection:
                connection.execute(
                    "UPDATE run_inputs SET input_text = '覆盖' WHERE run_id = ?",
                    (run["run_id"],),
                )
        with self.assertRaises(sqlite3.IntegrityError):
            with self.db.connect() as connection:
                connection.execute(
                    "UPDATE idea_runs SET config_snapshot = '{}' WHERE run_id = ?",
                    (run["run_id"],),
                )

    def test_status_can_change_without_changing_snapshot(self) -> None:
        case = self.db.create_case("状态测试")
        run = self.create_run(case["case_id"])
        self.db.set_run_status(run["run_id"], "RUNNING")
        self.db.set_run_status(
            run["run_id"],
            "COMPLETED_WITH_LIMITATIONS",
            limitations=[{"code": "ONE_PROVIDER"}],
        )
        updated = self.db.get_run(run["run_id"])
        self.assertEqual(updated["status"], "COMPLETED_WITH_LIMITATIONS")
        self.assertEqual(updated["limitation_json"], [{"code": "ONE_PROVIDER"}])
        self.assertIsNotNone(updated["started_at"])
        self.assertIsNotNone(updated["completed_at"])

    def test_manual_run_delete_records_minimal_event(self) -> None:
        case = self.db.create_case("删除测试")
        run = self.create_run(case["case_id"])
        self.db.delete_run(run["run_id"], "internal")
        with self.assertRaises(KeyError):
            self.db.get_run(run["run_id"])
        with self.db.connect() as connection:
            event = dict(connection.execute("SELECT * FROM deletion_events").fetchone())
        self.assertEqual(event["entity_type"], "run")
        self.assertEqual(event["entity_id"], run["run_id"])

    def test_manual_case_delete_cascades_runs(self) -> None:
        case = self.db.create_case("整案删除")
        run = self.create_run(case["case_id"])
        self.db.delete_case(case["case_id"])
        with self.assertRaises(KeyError):
            self.db.get_case(case["case_id"])
        with self.assertRaises(KeyError):
            self.db.get_run(run["run_id"])


if __name__ == "__main__":
    unittest.main()
