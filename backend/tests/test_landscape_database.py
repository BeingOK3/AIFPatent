from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from landscape.database import LandscapeDatabase
from landscape.schemas import AnalysisMode, LandscapeScope, RunStatus


class LandscapeDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "landscape.db"
        self.db = LandscapeDatabase(self.db_path)
        self.db.initialize()
        self.scope = LandscapeScope(
            mode=AnalysisMode.TECHNOLOGY,
            technology_direction="数据中心液冷",
            publication_start=date(2026, 4, 1),
            publication_end=date(2026, 7, 1),
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def create_run(self):
        return self.db.create_run(
            scope=self.scope,
            model="deepseek-v4-flash",
            workflow_version="1.0.0",
            prompt_version="1.0.0",
            config_snapshot={"provider": "google_patents"},
        )

    def test_schema_is_independent_and_complete(self) -> None:
        expected = {
            "landscape_runs", "landscape_steps", "landscape_stage_results",
            "landscape_queries", "landscape_hits", "landscape_run_documents",
            "landscape_evidence", "landscape_patent_analyses", "landscape_clusters",
            "landscape_cluster_members", "landscape_reports",
        }
        self.assertTrue(expected.issubset(self.db.table_names()))
        self.assertFalse(any(name.startswith("idea_") for name in self.db.table_names()))
        self.assertEqual(self.db_path.name, "landscape.db")

    def test_run_input_is_immutable_and_status_transitions_are_checked(self) -> None:
        run = self.create_run()
        with self.assertRaises(sqlite3.IntegrityError):
            with self.db.connect() as connection:
                connection.execute(
                    "UPDATE landscape_runs SET scope_json='{}' WHERE run_id=?", (run["run_id"],)
                )
        self.db.set_run_status(run["run_id"], RunStatus.RUNNING)
        self.db.set_run_status(run["run_id"], RunStatus.COMPLETED)
        with self.assertRaisesRegex(ValueError, "invalid run status transition"):
            self.db.set_run_status(run["run_id"], RunStatus.RUNNING)

    def test_stage_results_are_idempotent_and_write_once(self) -> None:
        run = self.create_run()
        first = self.db.put_stage_result(run["run_id"], "VALIDATE_SCOPE", {"valid": True})
        second = self.db.put_stage_result(run["run_id"], "VALIDATE_SCOPE", {"valid": True})
        self.assertEqual(first["content_hash"], second["content_hash"])
        with self.assertRaisesRegex(ValueError, "immutable"):
            self.db.put_stage_result(run["run_id"], "VALIDATE_SCOPE", {"valid": False})

    def test_combined_mode_is_persisted_and_listed_from_immutable_scope(self) -> None:
        scope = LandscapeScope(
            technology_direction="数据中心液冷",
            competitors=[{"name": "Huawei"}],
            publication_start=date(2026, 4, 1),
            publication_end=date(2026, 7, 1),
        )
        run = self.db.create_run(
            scope=scope,
            model="deepseek-v4-flash",
            workflow_version="1.0.0",
            prompt_version="1.0.0",
        )
        self.assertEqual(run["mode"], AnalysisMode.TECHNOLOGY_COMPETITOR.value)
        self.assertEqual(self.db.list_runs()[0]["mode"], AnalysisMode.TECHNOLOGY_COMPETITOR.value)

    def test_debug_snapshot_is_aggregated_and_excludes_raw_payloads(self) -> None:
        run = self.create_run()
        with self.db.connect() as connection:
            connection.execute(
                """
                INSERT INTO landscape_steps(
                    run_id,step_name,attempt,status,input_hash,output_hash,error_code,
                    error_message,started_at,completed_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (run["run_id"], "PLAN_SEARCH", 1, "SUCCEEDED", "in", "out", None, None, 10, 25),
            )
        self.db.put_queries(
            run["run_id"],
            [{"query_id": "LQ-1", "query_text": "液冷", "language": "zh", "rationale": "方向"}],
        )
        self.db.put_hit(
            run["run_id"],
            hit_id="LH-1",
            query_id="LQ-1",
            provider="fixture",
            publication_number="CN1A",
            application_number="CN1",
            publication_date="2026-05-01",
            assignee="Example",
            normalized_key="CN1A",
            decision="EXCLUDED",
            exclusion_reason="COMPETITOR_NOT_CONFIRMED",
            raw={"large_provider_payload": "must-not-be-returned"},
        )
        snapshot = self.db.debug_snapshot(run["run_id"])
        self.assertEqual(snapshot["steps"][0]["duration_ms"], 15)
        self.assertEqual(snapshot["queries"][0]["query_text"], "液冷")
        self.assertEqual(snapshot["hit_stats"][0]["count"], 1)
        self.assertNotIn("raw", snapshot["hit_stats"][0])
        self.assertNotIn("large_provider_payload", str(snapshot))

    def test_credentials_are_rejected_before_database_write(self) -> None:
        marker = "credential-must-never-persist"
        with self.assertRaisesRegex(ValueError, "sensitive field"):
            self.db.create_run(
                scope=self.scope,
                model="deepseek-v4-flash",
                workflow_version="1.0.0",
                prompt_version="1.0.0",
                config_snapshot={"api_key": marker},
            )
        self.assertNotIn(marker.encode(), self.db_path.read_bytes())

    def test_restart_marks_only_active_runs_failed(self) -> None:
        active = self.create_run()
        complete = self.create_run()
        self.db.set_run_status(complete["run_id"], RunStatus.RUNNING)
        self.db.set_run_status(complete["run_id"], RunStatus.COMPLETED)
        self.assertEqual(self.db.mark_interrupted_runs_failed(), 1)
        self.assertEqual(self.db.get_run(active["run_id"])["error_code"], "RUNTIME_API_KEY_REQUIRED_AFTER_RESTART")
        self.assertEqual(self.db.get_run(complete["run_id"])["status"], RunStatus.COMPLETED.value)

    def test_cluster_rows_are_idempotent_without_claiming_workflow_stage_result(self) -> None:
        run = self.create_run()
        clusters = [
            {
                "cluster_id": "CL-1",
                "name": "液冷回路",
                "summary": "冷板和循环泵。",
                "keywords": ["液冷"],
                "publication_numbers": ["CN1A"],
            }
        ]
        self.db.put_clusters(run["run_id"], clusters, {"CN1A": "doc-1"})
        self.db.put_clusters(run["run_id"], clusters, {"CN1A": "doc-1"})
        with self.assertRaises(KeyError):
            self.db.get_stage_result(run["run_id"], "CLUSTER_PATENTS")
        changed = [{**clusters[0], "summary": "不同内容"}]
        with self.assertRaisesRegex(ValueError, "immutable"):
            self.db.put_clusters(run["run_id"], changed, {"CN1A": "doc-1"})


if __name__ == "__main__":
    unittest.main()
