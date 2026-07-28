from __future__ import annotations

import unittest

from idea.postgres_database import _Connection, _Row, _translate_sql


class PostgreSQLDatabaseAdapterTests(unittest.TestCase):
    def test_sqlite_placeholder_and_syntax_are_translated(self) -> None:
        translated = _translate_sql(
            "BEGIN IMMEDIATE; SELECT * FROM idea_runs "
            "WHERE title = ? COLLATE NOCASE ORDER BY r.rowid LIMIT ?"
        )
        self.assertIn("BEGIN", translated)
        self.assertIn("%s", translated)
        self.assertNotIn("COLLATE NOCASE", translated)
        self.assertNotIn("r.rowid", translated)

    def test_json_placeholders_are_cast_for_legacy_repository_sql(self) -> None:
        insert = _translate_sql(
            """
            INSERT INTO landscape_runs(
                run_id,parent_run_id,status,mode,publication_start,publication_end,
                model,workflow_version,prompt_version,scope_json,config_snapshot,
                input_hash,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """
        )
        self.assertEqual(insert.count("%s::jsonb"), 2)

        positional = _translate_sql(
            "INSERT INTO landscape_stage_results VALUES(?,?,?,?,?)"
        )
        self.assertEqual(positional, "INSERT INTO landscape_stage_results VALUES(%s,%s,%s::jsonb,%s,%s)")

        update = _translate_sql(
            "UPDATE landscape_steps SET output_json=? WHERE run_id=?"
        )
        self.assertIn("output_json=%s::jsonb", update)

    def test_sqlite_schema_probe_is_not_executed_in_postgres(self) -> None:
        translated = _translate_sql(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='landscape_runs'"
        )
        self.assertEqual(translated, "SELECT 'TECHNOLOGY_COMPETITOR' AS sql")

    def test_legacy_boolean_predicates_are_translated(self) -> None:
        self.assertIn(
            "deep_reviewed = FALSE",
            _translate_sql("SELECT 1 FROM run_documents WHERE deep_reviewed = 0"),
        )
        self.assertIn(
            "deep_reviewed = TRUE",
            _translate_sql("SELECT 1 FROM run_documents WHERE deep_reviewed = 1"),
        )

    def test_row_supports_dict_and_legacy_positional_access(self) -> None:
        row = _Row({"run_id": "r1", "status": "QUEUED"})
        self.assertEqual(row["run_id"], "r1")
        self.assertEqual(row[0], "r1")
        self.assertEqual(dict(row)["status"], "QUEUED")

    def test_connection_translates_queries_without_exposing_driver(self) -> None:
        class Cursor:
            rowcount = 1

            def fetchone(self):
                return {"value": {"nested": True}}

            def fetchall(self):
                return [{"value": ["a", "b"]}]

        class Raw:
            def __init__(self):
                self.queries = []

            def execute(self, query, params):
                self.queries.append((query, params))
                return Cursor()

            def commit(self):
                pass

            def rollback(self):
                pass

            def close(self):
                pass

        raw = Raw()
        connection = _Connection(raw)
        self.assertEqual(connection.execute("SELECT value FROM t WHERE id = ?", ("x",)).fetchone()[0], '{"nested":true}')
        self.assertEqual(connection.execute("SELECT value FROM t").fetchall()[0]["value"], '["a","b"]')
        self.assertEqual(raw.queries[0], ("SELECT value FROM t WHERE id = %s", ("x",)))


if __name__ == "__main__":
    unittest.main()
