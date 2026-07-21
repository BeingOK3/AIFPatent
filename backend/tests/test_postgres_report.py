from __future__ import annotations

import asyncio
import unittest

from idea.chunks import PatentChunk
from idea.lexical import LexicalHit
from idea.postgres_report import PostgreSQLReportScopeRepository
from idea.report_retrieval import (
    ReportEvidenceHit,
    ReportRetrievalQuery,
    ReportRetrievalResult,
    ReportRetrievalSelection,
)


class FakeCursor:
    def __init__(self, rows=(), count=0):
        self.rows = rows
        self.count = count
        self.executions = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def execute(self, query, parameters):
        self.executions.append((query, parameters))

    async def fetchall(self):
        return self.rows

    async def fetchone(self):
        return (self.count,)


class FakeConnection:
    def __init__(self, rows=(), count=0):
        self.cursor_instance = FakeCursor(rows, count)
        self.committed = False
        self.rolled_back = False

    def cursor(self):
        return self.cursor_instance

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True

    async def close(self):
        return None


class PostgreSQLReportScopeRepositoryTests(unittest.TestCase):
    def test_scope_query_requires_ready_deep_reviewed_versions(self) -> None:
        connection = FakeConnection(rows=(("doc-1", "cv-1", "CN1A"),))

        async def connect(_dsn):
            return connection

        result = asyncio.run(
            PostgreSQLReportScopeRepository("postgresql://test", connect=connect)
            .load_ready_deep_reviewed("run-1")
        )

        sql, parameters = connection.cursor_instance.executions[0]
        self.assertIn("rdv.corpus_availability = 'READY'", sql)
        self.assertIn("rdv.deep_reviewed = TRUE", sql)
        self.assertIn("pv.state = 'READY'", sql)
        self.assertEqual(parameters, ("run-1",))
        self.assertEqual(result[0].version_id, "cv-1")

    def test_pre_analysis_scope_is_checkpoint_version_bound(self) -> None:
        connection = FakeConnection(rows=(("doc-1", "cv-1", "CN1A"),))

        async def connect(_dsn):
            return connection

        result = asyncio.run(
            PostgreSQLReportScopeRepository("postgresql://test", connect=connect)
            .load_ready_for_analysis("run-1", ("cv-1",))
        )

        sql, parameters = connection.cursor_instance.executions[0]
        self.assertIn("rdv.version_id = ANY(%s)", sql)
        self.assertIn("rdv.corpus_availability = 'READY'", sql)
        self.assertNotIn("deep_reviewed = TRUE", sql)
        self.assertEqual(parameters, ("run-1", ["cv-1"]))
        self.assertEqual(result[0].publication_number, "CN1A")

    def test_persisted_hit_keeps_query_score_and_match_kind(self) -> None:
        connection = FakeConnection(count=1)

        async def connect(_dsn):
            return connection

        chunk = PatentChunk(
            chunk_id="chunk-1", version_id="cv-1", publication_number="CN1A",
            section_type="claims", section_label="claim-1", claim_number=1,
            claim_kind="independent", parent_claim_numbers=(), start_offset=0,
            end_offset=10, text="claim text", text_hash="a" * 64,
            token_count=2, chunker_version="v1",
        )
        hit = LexicalHit("RQ-1", 2, 0.75, "fts", chunk)
        result = ReportRetrievalResult(
            run_id="run-1", retriever_version="rv1", corpus_version_ids=("cv-1",),
            queries=(ReportRetrievalQuery(
                "RQ-1", "F1", "claim", "doc-1", "cv-1", "CN1A", 1
            ),),
            selections=(ReportRetrievalSelection(
                "F1", "doc-1", "cv-1", "CN1A",
                ReportEvidenceHit.from_lexical(hit),
            ),),
        )

        asyncio.run(
            PostgreSQLReportScopeRepository("postgresql://test", connect=connect)
            .persist(result)
        )

        hit_sql, hit_parameters = connection.cursor_instance.executions[1]
        self.assertIn("query_id, lexical_score, match_kind", hit_sql)
        self.assertEqual(hit_parameters[9:12], ("RQ-1", 0.75, "fts"))
        self.assertEqual(hit_parameters[12:15], (None, None, 2))


if __name__ == "__main__":
    unittest.main()
