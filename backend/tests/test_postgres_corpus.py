from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone

from idea.corpus import CorpusRunLink, CorpusVersion
from idea.postgres_corpus import (
    PostgreSQLCorpusError,
    PostgreSQLCorpusRunLinkRepository,
    PostgreSQLCorpusVersionRepository,
)


class FakeCursor:
    def __init__(self) -> None:
        self.executions: list[tuple[str, tuple[object, ...]]] = []
        self.rowcount = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def execute(self, query: str, parameters: tuple[object, ...]) -> None:
        self.executions.append((query, parameters))
        self.rowcount = len(parameters[1])

    async def fetchall(self):
        return [(document_id,) for document_id in self.executions[0][1][1]]


class FakeConnection:
    def __init__(self) -> None:
        self.cursor_instance = FakeCursor()
        self.committed = False
        self.rolled_back = False

    def cursor(self):
        return self.cursor_instance

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True

    async def close(self) -> None:
        return None


class PostgreSQLCorpusContractTests(unittest.TestCase):
    def test_repository_requires_dsn_and_rejects_mismatched_key(self) -> None:
        with self.assertRaises(ValueError):
            PostgreSQLCorpusVersionRepository("")
        self.assertTrue(issubclass(PostgreSQLCorpusError, RuntimeError))

    def test_version_conversion_preserves_immutable_identity(self) -> None:
        digest = "a" * 64
        row = {
            "version_id": "cv-1",
            "document_id": "doc-1",
            "publication_number": "CN123",
            "language": "en",
            "normalized_content_hash": digest,
            "state": "READY",
            "metadata_json": {"provider": "fixture"},
            "created_at": 1_700_000_000_000,
            "object_key": "patent-corpus/CN123/" + digest + ".json",
            "uncompressed_bytes": 42,
        }
        version = PostgreSQLCorpusVersionRepository._row_to_version(row)
        self.assertEqual(version.document_id, "doc-1")
        self.assertEqual(version.provider, "fixture")
        self.assertEqual(version.content_sha256, digest)
        self.assertEqual(version.status, "READY")
        self.assertEqual(version.created_at.tzinfo, timezone.utc)

    def test_millis_conversion_is_deterministic(self) -> None:
        value = datetime(2026, 7, 21, tzinfo=timezone.utc)
        self.assertEqual(PostgreSQLCorpusVersionRepository._millis(value), 1784592000000)

    def test_run_link_conversion_preserves_frozen_version(self) -> None:
        row = {
            "run_id": "run-1",
            "document_id": "doc-1",
            "version_id": "cv-1",
            "corpus_availability": "READY",
            "deep_reviewed": True,
            "linked_at": 1_700_000_000_000,
        }

        link = PostgreSQLCorpusRunLinkRepository._row_to_link(row)

        self.assertEqual(
            link,
            CorpusRunLink(
                run_id="run-1",
                document_id="doc-1",
                version_id="cv-1",
                corpus_availability="READY",
                deep_reviewed=True,
                linked_at=datetime.fromtimestamp(1_700_000_000, tz=timezone.utc),
            ),
        )

    def test_deep_review_state_is_synchronized_to_run_document(self) -> None:
        connection = FakeConnection()

        async def connect(_dsn: str):
            return connection

        repository = PostgreSQLCorpusRunLinkRepository("postgresql://test", connect=connect)
        asyncio.run(repository.mark_deep_reviewed("run-1", ("doc-1", "doc-2")))

        self.assertTrue(connection.committed)
        self.assertFalse(connection.rolled_back)
        self.assertEqual(len(connection.cursor_instance.executions), 2)
        self.assertIn("UPDATE run_document_versions", connection.cursor_instance.executions[0][0])
        self.assertIn("UPDATE run_documents", connection.cursor_instance.executions[1][0])


if __name__ == "__main__":
    unittest.main()
