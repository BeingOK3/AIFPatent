from __future__ import annotations

import asyncio
import hashlib
import unittest

from idea.citations import CitationVerificationError
from idea.postgres_citations import PostgreSQLCitationRepository


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.executions = []

    async def __aenter__(self): return self
    async def __aexit__(self, exc_type, exc, traceback): return None
    async def execute(self, query, parameters): self.executions.append((query, parameters))
    async def fetchall(self): return self.rows


class FakeConnection:
    def __init__(self, rows): self.cursor_instance = FakeCursor(rows)
    def cursor(self): return self.cursor_instance
    async def close(self): return None


class PostgreSQLCitationRepositoryTests(unittest.TestCase):
    def row(self, *, excerpt="claim text"):
        digest = hashlib.sha256("claim text".encode()).hexdigest()
        binding = {
            "alias": "C1", "chunk_id": "chunk-1", "version_id": "cv-1",
            "publication_number": "CN1A", "section_type": "claims",
            "section_label": "claim-1", "claim_number": 1,
            "start_offset": 0, "end_offset": 10, "text_hash": digest,
            "excerpt": excerpt,
        }
        return (
            "run-1:F1", "CTX-fixture", binding, "chunk-1", "cv-1", "CN1A",
            "claims", "claim-1", 1, 0, 10, "claim text", digest,
        )

    def test_query_is_run_scoped_and_returns_verified_rows(self) -> None:
        connection = FakeConnection((self.row(),))
        async def connect(_dsn): return connection

        citations = asyncio.run(
            PostgreSQLCitationRepository("postgresql://test", connect=connect)
            .for_run("run-1")
        )

        sql, parameters = connection.cursor_instance.executions[0]
        self.assertIn("h.run_id = %s", sql)
        self.assertIn("h.selected_for_context = TRUE", sql)
        self.assertEqual(parameters, ("run-1",))
        self.assertEqual(citations[0].feature_id, "F1")

    def test_tampered_manifest_excerpt_is_rejected(self) -> None:
        connection = FakeConnection((self.row(excerpt="rewritten"),))
        async def connect(_dsn): return connection
        with self.assertRaises(CitationVerificationError):
            asyncio.run(
                PostgreSQLCitationRepository("postgresql://test", connect=connect)
                .for_run("run-1")
            )


if __name__ == "__main__":
    unittest.main()
