from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone

from idea.chunks import PatentChunker
from idea.context import ContextAssembler
from idea.corpus import CorpusVersion
from idea.postgres_context import PostgreSQLContextRepository
from idea.providers import FetchedDocument


class FakeCursor:
    def __init__(self, context_hash):
        self.context_hash = context_hash
        self.executions = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def execute(self, query, parameters):
        self.executions.append((query, parameters))

    async def fetchone(self):
        return (self.context_hash,)


class FakeConnection:
    def __init__(self, context_hash):
        self.cursor_instance = FakeCursor(context_hash)
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


class PostgreSQLContextRepositoryTests(unittest.TestCase):
    def test_manifest_write_contains_citation_bindings_but_no_secret(self) -> None:
        version = CorpusVersion(
            version_id="cv-1", publication_number="CN1A", language="en",
            provider="fixture", object_key="corpus/CN1A/hash.json",
            content_sha256="a" * 64, normalized_size=10,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        chunks = PatentChunker().chunk(
            version,
            FetchedDocument(
                provider="fixture", publication_number="CN1A",
                url="https://example.test/CN1A", abstract_text="cache abstract",
                claims_text="1. A cache controller.",
            ),
        )
        context = ContextAssembler().assemble(
            purpose="INITIAL_REVIEW", run_id="run-1",
            corpus_snapshot_hash="b" * 64, chunks=chunks,
            system_prompt="Analyze evidence.", question="cache eviction",
            prompt_version="document-analysis-v2",
            retriever_version="initial-report-lexical-v1",
            input_budget=100, reserved_output_tokens=20,
        )
        connection = FakeConnection(context.context_hash)

        async def connect(_dsn):
            return connection

        asyncio.run(
            PostgreSQLContextRepository("postgresql://test", connect=connect)
            .put_if_absent(context, agent_name="patent-document-analyzer")
        )

        self.assertTrue(connection.committed)
        sql, parameters = connection.cursor_instance.executions[0]
        self.assertIn("INSERT INTO model_context_manifests", sql)
        encoded = " ".join(str(value) for value in parameters)
        self.assertIn(chunks[0].chunk_id, encoded)
        self.assertIn("cache abstract", encoded)
        self.assertNotIn("api_key", encoded.lower())
        self.assertNotIn("secret", encoded.lower())


if __name__ == "__main__":
    unittest.main()
