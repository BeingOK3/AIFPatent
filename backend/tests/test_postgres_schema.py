from __future__ import annotations

import unittest
from pathlib import Path


SCHEMA_PATH = Path("deploy/rag/postgres-init/010_core_schema.sql")


class PostgreSQLSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sql = SCHEMA_PATH.read_text(encoding="utf-8")

    def test_bridge_schema_is_idempotent_and_has_required_tables(self) -> None:
        self.assertIn("BEGIN;", self.sql)
        self.assertIn("COMMIT;", self.sql)
        for table in (
            "aifpatent_schema_migrations",
            "idea_cases",
            "idea_runs",
            "run_inputs",
            "run_steps",
            "patent_documents",
            "evidence",
            "reports",
            "model_context_manifests",
        ):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", self.sql)
        self.assertIn("ON CONFLICT (version) DO NOTHING", self.sql)

    def test_schema_preserves_immutability_and_json_boundaries(self) -> None:
        self.assertIn("CREATE OR REPLACE FUNCTION prevent_immutable_run_fields_update", self.sql)
        self.assertIn("CREATE OR REPLACE FUNCTION prevent_run_input_update", self.sql)
        self.assertGreaterEqual(self.sql.count("JSONB"), 15)
        self.assertIn("allowed_version_ids_json JSONB NOT NULL", self.sql)
        self.assertIn("purpose TEXT NOT NULL CHECK", self.sql)

    def test_schema_has_no_data_destructive_commands(self) -> None:
        upper = self.sql.upper()
        self.assertNotIn("DROP TABLE", upper)
        self.assertNotIn("TRUNCATE", upper)
        self.assertNotIn("DELETE FROM", upper)
        self.assertNotIn("POSTGRES_PASSWORD=", upper)


if __name__ == "__main__":
    unittest.main()
