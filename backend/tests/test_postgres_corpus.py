from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace
from datetime import datetime, timezone

from idea.chunks import PatentChunk
from idea.corpus import CorpusRunLink, CorpusVersion
from idea.postgres_corpus import (
    PostgreSQLCorpusError,
    PostgreSQLCorpusPrerequisiteRepository,
    PostgreSQLCorpusRunLinkRepository,
    PostgreSQLCorpusVersionRepository,
    PostgreSQLPatentChunkRepository,
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


class SyncCursor:
    def __init__(self, row=("COMPLETED", [], 1, 2, None, None)) -> None:
        self.row = row
        self.executions = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def execute(self, query, parameters):
        self.executions.append((query, parameters))

    async def fetchone(self):
        return self.row


class SyncConnection(FakeConnection):
    def __init__(self, row=("COMPLETED", [], 1, 2, None, None)) -> None:
        super().__init__()
        self.cursor_instance = SyncCursor(row)


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

    def test_terminal_run_status_sync_updates_only_mutable_bridge_fields(self) -> None:
        connection = SyncConnection()

        class SourceDatabase:
            @staticmethod
            def get_run(_run_id):
                return {
                    "status": "COMPLETED",
                    "limitation_json": [],
                    "started_at": 1,
                    "completed_at": 2,
                    "error_code": None,
                    "error_message": None,
                }

        async def connect(_dsn):
            return connection

        synced = asyncio.run(
            PostgreSQLCorpusPrerequisiteRepository(
                SourceDatabase(), "postgresql://test", connect=connect
            ).sync_run_status("run-1")
        )

        self.assertTrue(synced)
        sql, parameters = connection.cursor_instance.executions[0]
        self.assertIn("UPDATE idea_runs", sql)
        self.assertIn("status = %s", sql)
        for immutable in ("case_id", "evaluation_date", "model", "config_snapshot"):
            self.assertNotIn(f"{immutable} =", sql)
        self.assertEqual(parameters[-1], "run-1")
        self.assertTrue(connection.committed)

    def test_run_status_sync_is_noop_when_run_was_never_bridged(self) -> None:
        connection = SyncConnection(row=None)

        class SourceDatabase:
            @staticmethod
            def get_run(_run_id):
                return {
                    "status": "FAILED", "limitation_json": [],
                    "started_at": None, "completed_at": 2,
                    "error_code": "FAIL", "error_message": "failed",
                }

        async def connect(_dsn):
            return connection

        synced = asyncio.run(
            PostgreSQLCorpusPrerequisiteRepository(
                SourceDatabase(), "postgresql://test", connect=connect
            ).sync_run_status("run-missing")
        )

        self.assertFalse(synced)
        self.assertTrue(connection.rolled_back)

    def test_chunk_conversion_preserves_structure_and_offsets(self) -> None:
        row = {
            "chunk_id": "chunk-1",
            "version_id": "cv-1",
            "publication_number": "CN123",
            "section_type": "claims",
            "section_label": "claim-2",
            "claim_number": 2,
            "claim_kind": "dependent",
            "parent_claims_json": [1],
            "start_offset": 20,
            "end_offset": 60,
            "text": "2. The system of claim 1.",
            "text_hash": "b" * 64,
            "token_count": 6,
            "chunker_version": "claims-paragraphs-v1",
        }

        chunk = PostgreSQLPatentChunkRepository._row_to_chunk(row)

        self.assertEqual(
            chunk,
            PatentChunk(
                chunk_id="chunk-1",
                version_id="cv-1",
                publication_number="CN123",
                section_type="claims",
                section_label="claim-2",
                claim_number=2,
                claim_kind="dependent",
                parent_claim_numbers=(1,),
                start_offset=20,
                end_offset=60,
                text="2. The system of claim 1.",
                text_hash="b" * 64,
                token_count=6,
                chunker_version="claims-paragraphs-v1",
            ),
        )

    def test_chunk_write_rejects_mixed_chunker_versions_before_connecting(self) -> None:
        chunk = PostgreSQLPatentChunkRepository._row_to_chunk(
            {
                "chunk_id": "chunk-1",
                "version_id": "cv-1",
                "publication_number": "CN123",
                "section_type": "abstract",
                "section_label": "abstract",
                "claim_number": None,
                "claim_kind": None,
                "parent_claims_json": [],
                "start_offset": 0,
                "end_offset": 4,
                "text": "text",
                "text_hash": "b" * 64,
                "token_count": 1,
                "chunker_version": "v1",
            }
        )

        async def unexpected_connect(_dsn: str):
            raise AssertionError("invalid input must fail before connecting")

        repository = PostgreSQLPatentChunkRepository(
            "postgresql://test", connect=unexpected_connect
        )
        with self.assertRaisesRegex(PostgreSQLCorpusError, "single Chunker version"):
            asyncio.run(
                repository.put_many_if_absent(
                    (chunk, replace(chunk, chunk_id="chunk-2", chunker_version="v2"))
                )
            )


if __name__ == "__main__":
    unittest.main()
