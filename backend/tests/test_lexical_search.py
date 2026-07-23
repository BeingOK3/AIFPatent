from __future__ import annotations

import asyncio
import unittest

from idea.lexical import LexicalSearchRequest, lexical_query_terms, lexical_search_terms
from idea.postgres_lexical import PostgreSQLLexicalSearchRepository


class FakeCursor:
    def __init__(self, rows=()) -> None:
        self.rows = rows
        self.executions = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def execute(self, query, parameters):
        self.executions.append((query, parameters))

    async def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, rows=()) -> None:
        self.cursor_instance = FakeCursor(rows)

    def cursor(self):
        return self.cursor_instance

    async def close(self):
        return None


class RepairCursor(FakeCursor):
    def __init__(self) -> None:
        super().__init__()
        self.phase = ""

    async def execute(self, query, parameters):
        await super().execute(query, parameters)
        if "SELECT chunk_id" in query:
            self.phase = "select"
        elif "SELECT COUNT(*)" in query:
            self.phase = "verify"

    async def fetchall(self):
        if self.phase == "select":
            return [
                ("chunk-1", "cv-1", "CN123", "claims", "claim-1", "缓存淘汰 claim", {}),
            ]
        return []

    async def fetchone(self):
        return (0,)


class RepairConnection(FakeConnection):
    def __init__(self) -> None:
        self.cursor_instance = RepairCursor()
        self.committed = False
        self.rolled_back = False

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True


class LexicalTokenizerTests(unittest.TestCase):
    def test_terms_are_versioned_normalized_and_cover_chinese_bigrams(self) -> None:
        terms = lexical_search_terms(
            text="GPU缓存淘汰 uses Token-Heat.",
            publication_number="CN-123-A",
            section_type="claims",
            section_label="claim-1",
        )

        self.assertEqual(terms.version, "patent-lexical-v1")
        tokens = terms.value.split()
        self.assertIn("gpu", tokens)
        self.assertIn("缓存", tokens)
        self.assertIn("存淘", tokens)
        self.assertIn("淘汰", tokens)
        self.assertIn("token-heat", tokens)
        self.assertIn("cn-123-a", tokens)

    def test_request_requires_explicit_unique_version_scope(self) -> None:
        with self.assertRaisesRegex(ValueError, "allowed Version"):
            LexicalSearchRequest(query_id="q1", text="cache", allowed_version_ids=())
        with self.assertRaisesRegex(ValueError, "unique"):
            LexicalSearchRequest(
                query_id="q1",
                text="cache",
                allowed_version_ids=("cv-1", "cv-1"),
            )

    def test_chinese_query_uses_bigrams_without_full_run_conjunct(self) -> None:
        terms = lexical_query_terms("缓存淘汰方法")

        self.assertEqual(terms.value.split(), ["缓存", "存淘", "淘汰", "汰方", "方法"])
        self.assertNotIn("缓存淘汰方法", terms.value.split())


class PostgreSQLLexicalSearchTests(unittest.TestCase):
    def test_search_is_parameterized_scoped_and_deterministically_mapped(self) -> None:
        connection = FakeConnection(
            rows=(
                (
                    "chunk-1", "cv-1", "CN123", "claims", "claim-1",
                    1, "independent", [], 0, 20, "cache eviction claim",
                    "a" * 64, 3, "claims-paragraphs-v1", 0.75, "fts",
                ),
            )
        )

        async def connect(_dsn):
            return connection

        repository = PostgreSQLLexicalSearchRepository(
            "postgresql://test", connect=connect
        )
        request = LexicalSearchRequest(
            query_id="q1",
            text="cache eviction",
            allowed_version_ids=("cv-1", "cv-2"),
            section_types=("claims",),
            limit=30,
        )

        hits = asyncio.run(repository.search(request))

        sql, parameters = connection.cursor_instance.executions[0]
        self.assertNotIn(request.text, sql)
        self.assertIn("version_id = ANY(%s)", sql)
        self.assertIn("active_chunkers", sql)
        self.assertIn("lexical_tokenizer_version", sql)
        self.assertIn("search_text %%> q.needle", sql)
        self.assertNotIn("similarity(c.search_text", sql)
        self.assertEqual(parameters[0], ["cv-1", "cv-2"])
        self.assertEqual(parameters[3], ["claims"])
        self.assertEqual(hits[0].rank, 1)
        self.assertEqual(hits[0].query_id, "q1")
        self.assertEqual(hits[0].chunk.chunk_id, "chunk-1")

    def test_repair_rebuilds_versioned_terms_and_verifies_scope(self) -> None:
        connection = RepairConnection()

        async def connect(_dsn):
            return connection

        repository = PostgreSQLLexicalSearchRepository(
            "postgresql://test", connect=connect
        )

        repaired = asyncio.run(repository.repair(("cv-1",)))

        self.assertEqual(repaired, 1)
        self.assertTrue(connection.committed)
        update = next(
            item for item in connection.cursor_instance.executions
            if "UPDATE patent_chunks" in item[0]
        )
        self.assertIn("缓存", update[1][0])
        self.assertEqual(update[1][-1], "chunk-1")

    def test_repair_rejects_partially_missing_version_scope(self) -> None:
        connection = RepairConnection()

        async def connect(_dsn):
            return connection

        repository = PostgreSQLLexicalSearchRepository(
            "postgresql://test", connect=connect
        )

        with self.assertRaisesRegex(RuntimeError, "scope is incomplete"):
            asyncio.run(repository.repair(("cv-1", "cv-missing")))

        self.assertTrue(connection.rolled_back)
        self.assertFalse(connection.committed)


if __name__ == "__main__":
    unittest.main()
