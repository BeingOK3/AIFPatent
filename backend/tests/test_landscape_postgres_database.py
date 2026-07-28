from __future__ import annotations

import unittest
from contextlib import contextmanager

from landscape.postgres_database import (
    LandscapePostgreSQLDatabase,
    _prepare_candidate_rows,
)


class _Cursor:
    def __init__(self, rows=None):
        self._rows = list(rows or [])
        self.rowcount = len(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _CandidateConnection:
    def __init__(self):
        self.rows: list[dict] = []
        self.statements: list[str] = []

    def execute(self, sql, params=()):
        self.statements.append(sql)
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT document_id"):
            return _Cursor(
                sorted(
                    self.rows,
                    key=lambda row: (row["rank"], row["publication_number"]),
                )
            )
        if normalized.startswith("INSERT INTO landscape_candidates"):
            (
                run_id,
                document_id,
                publication_number,
                normalized_key,
                rank,
                decision,
                metadata_json,
                content_hash,
                created_at,
            ) = params
            self.rows.append(
                {
                    "run_id": run_id,
                    "document_id": document_id,
                    "publication_number": publication_number,
                    "normalized_key": normalized_key,
                    "rank": rank,
                    "decision": decision,
                    "metadata_json": metadata_json,
                    "content_hash": content_hash,
                    "created_at": created_at,
                }
            )
            return _Cursor()
        raise AssertionError(f"unexpected SQL: {normalized}")


class LandscapePostgreSQLCandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = LandscapePostgreSQLDatabase(
            "postgresql://test:test@localhost/test"
        )
        self.connection = _CandidateConnection()

        @contextmanager
        def connect():
            yield self.connection

        self.database.connect = connect  # type: ignore[method-assign]

    @staticmethod
    def candidates():
        return [
            {
                "document_id": "LD-2",
                "publication_number": "US 2024/001 A1",
                "normalized_key": "US2024001A1",
                "rank": 2,
                "decision": "ELIGIBLE",
                "metadata": {"title": "Second", "found_by": ["fixture"]},
            },
            {
                "document_id": "LD-1",
                "publication_number": "CN123A",
                "normalized_key": "CN123A",
                "rank": 1,
                "decision": "ELIGIBLE",
                "metadata": {"title": "First"},
            },
        ]

    def test_candidate_set_is_written_once_with_explicit_jsonb_and_read_by_rank(self) -> None:
        candidates = self.candidates()
        candidates[0]["publication_number"] = "US2024001A1"
        first = self.database.put_candidates("run-1", candidates)
        second = self.database.put_candidates("run-1", candidates)

        self.assertEqual([item["rank"] for item in first], [1, 2])
        self.assertEqual(first, second)
        self.assertEqual(len(self.connection.rows), 2)
        inserts = [
            sql
            for sql in self.connection.statements
            if "INSERT INTO landscape_candidates" in sql
        ]
        self.assertEqual(len(inserts), 2)
        self.assertTrue(all("%s::jsonb" in sql for sql in inserts))

    def test_existing_candidate_set_rejects_any_content_change(self) -> None:
        candidates = self.candidates()
        candidates[0]["publication_number"] = "US2024001A1"
        self.database.put_candidates("run-1", candidates)
        changed = self.candidates()
        changed[0]["publication_number"] = "US2024001A1"
        changed[0]["metadata"] = {"title": "Changed"}
        with self.assertRaisesRegex(ValueError, "immutable"):
            self.database.put_candidates("run-1", changed)

    def test_candidate_identity_must_be_canonical_and_ranks_contiguous(self) -> None:
        with self.assertRaisesRegex(ValueError, "canonical publication number"):
            _prepare_candidate_rows(self.candidates())
        invalid_ranks = [
            {
                "document_id": "LD-1",
                "publication_number": "CN123A",
                "normalized_key": "CN123A",
                "rank": 2,
                "metadata": {},
            }
        ]
        with self.assertRaisesRegex(ValueError, "contiguous"):
            _prepare_candidate_rows(invalid_ranks)

    def test_candidate_metadata_rejects_secrets(self) -> None:
        candidate = {
            "document_id": "LD-1",
            "publication_number": "CN123A",
            "normalized_key": "CN123A",
            "rank": 1,
            "metadata": {"provider_api_key": "must-not-persist"},
        }
        with self.assertRaisesRegex(ValueError, "sensitive field"):
            self.database.put_candidates("run-1", [candidate])
        self.assertEqual(self.connection.rows, [])


if __name__ == "__main__":
    unittest.main()
