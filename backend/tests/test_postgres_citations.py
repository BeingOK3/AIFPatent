from __future__ import annotations

import asyncio
import hashlib
import unittest
from types import SimpleNamespace

from idea.citations import CitationVerificationError, ModelCitationSelection
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
    def __init__(self, rows=()):
        self.cursor_instance = FakeCursor(rows)
        self.committed = False
        self.rolled_back = False
    def cursor(self): return self.cursor_instance
    async def commit(self): self.committed = True
    async def rollback(self): self.rolled_back = True
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
            "claims", "claim-1", "1", 0, 10, "claim text", digest,
            "a" * 64, "prompt-v1", "retriever-v1", "b" * 64,
            "READY", True, "READY",
        )

    def test_query_is_run_scoped_and_returns_verified_rows(self) -> None:
        connection = FakeConnection((self.row(),))
        async def connect(_dsn): return connection

        citations = asyncio.run(
            PostgreSQLCitationRepository("postgresql://test", connect=connect)
            .for_run("run-1")
        )

        sql, parameters = connection.cursor_instance.executions[0]
        self.assertIn("mc.run_id = %s", sql)
        self.assertIn("report_model_citations", sql)
        self.assertIn("run_document_versions", sql)
        self.assertIn("patent_document_versions", sql)
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

    def test_records_only_model_selected_alias_chunk_bindings(self) -> None:
        connection = FakeConnection()
        async def connect(_dsn): return connection
        context = SimpleNamespace(
            run_id="run-1",
            purpose="INITIAL_REVIEW",
            context_id="CTX-fixture",
            selected_chunks=({"alias": "C1", "chunk_id": "chunk-1"},),
        )

        asyncio.run(
            PostgreSQLCitationRepository("postgresql://test", connect=connect).record(
                run_id="run-1",
                document_id="doc-1",
                context=context,
                selections=(ModelCitationSelection("F1", "C1", "chunk-1"),),
            )
        )

        delete_sql, delete_parameters = connection.cursor_instance.executions[0]
        self.assertIn("DELETE FROM report_model_citations", delete_sql)
        self.assertEqual(delete_parameters, ("run-1", "doc-1", "CTX-fixture"))
        sql, parameters = connection.cursor_instance.executions[1]
        self.assertIn("INSERT INTO report_model_citations", sql)
        self.assertEqual(parameters[2:6], ("run-1:F1", "CTX-fixture", "C1", "chunk-1"))
        self.assertTrue(connection.committed)

    def test_empty_retry_atomically_removes_stale_model_citations(self) -> None:
        connection = FakeConnection()
        async def connect(_dsn): return connection
        context = SimpleNamespace(
            run_id="run-1", purpose="INITIAL_REVIEW", context_id="CTX-fixture",
            selected_chunks=({"alias": "C1", "chunk_id": "chunk-1"},),
        )
        asyncio.run(
            PostgreSQLCitationRepository("postgresql://test", connect=connect).record(
                run_id="run-1", document_id="doc-1", context=context, selections=(),
            )
        )
        self.assertEqual(len(connection.cursor_instance.executions), 1)
        self.assertIn("DELETE FROM report_model_citations", connection.cursor_instance.executions[0][0])
        self.assertTrue(connection.committed)

    def test_record_rejects_alias_chunk_not_in_context(self) -> None:
        async def connect(_dsn): return FakeConnection()
        context = SimpleNamespace(
            run_id="run-1", purpose="INITIAL_REVIEW", context_id="CTX-fixture",
            selected_chunks=({"alias": "C1", "chunk_id": "chunk-1"},),
        )
        with self.assertRaisesRegex(CitationVerificationError, "alias/chunk"):
            asyncio.run(
                PostgreSQLCitationRepository("postgresql://test", connect=connect).record(
                    run_id="run-1", document_id="doc-1", context=context,
                    selections=(ModelCitationSelection("F1", "C1", "chunk-other"),),
                )
            )


if __name__ == "__main__":
    unittest.main()
