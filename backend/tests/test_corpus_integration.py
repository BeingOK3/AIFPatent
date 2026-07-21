from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

from idea.corpus import PatentCorpusIngestService, PatentCorpusService
from idea.corpus_migration import HistoricalCorpusMigrator, MigrationStatus
from idea.chunks import PatentChunkPersistenceService
from idea.database import Database
from idea.lexical import LexicalSearchRequest
from idea.postgres_corpus import (
    PostgreSQLCorpusError,
    PostgreSQLCorpusPrerequisiteRepository,
    PostgreSQLCorpusRunLinkRepository,
    PostgreSQLCorpusVersionRepository,
    PostgreSQLCorpusVersionSourceRepository,
    PostgreSQLPatentChunkRepository,
)
from idea.providers import FetchedDocument
from idea.postgres_lexical import PostgreSQLLexicalSearchRepository
from idea.s3_object_store import S3ObjectStore


@unittest.skipUnless(
    os.environ.get("AIFPATENT_RUN_RAG_INTEGRATION") == "1",
    "set AIFPATENT_RUN_RAG_INTEGRATION=1 to test PostgreSQL and S3",
)
class CorpusIntegrationTests(unittest.TestCase):
    def test_ingest_is_durable_idempotent_and_synchronizes_review_state(self) -> None:
        asyncio.run(self._run_scenario())

    def test_historical_local_text_migrates_once_into_real_corpus(self) -> None:
        asyncio.run(self._run_historical_migration_scenario())

    async def _run_historical_migration_scenario(self) -> None:
        suffix = uuid.uuid4().hex
        document_id = f"integration-migrate-doc-{suffix}"
        publication_number = f"INTEGRATION-MIGRATE-{suffix}"
        abstract = "Historical durable migration content."
        claims = "1. A historical migration claim."
        description = "Historical migration description."
        content_hash = hashlib.sha256(
            "\n\n".join((abstract, claims, description)).encode("utf-8")
        ).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "idea.sqlite3")
            database.initialize()
            case = database.create_case(f"Corpus migration integration {suffix}")
            run = database.create_run(
                case_id=case["case_id"],
                input_text="Verify historical corpus migration.",
                evaluation_date="2026-07-21",
                date_basis="publication",
                analysis_scope="integration",
                model="fixture",
                skill_version="integration/1",
                workflow_version="integration/1",
                config_snapshot={},
            )
            with database.connect() as connection:
                connection.execute(
                    "UPDATE idea_runs SET status = 'COMPLETED' WHERE run_id = ?",
                    (run["run_id"],),
                )
                connection.execute(
                    """
                    INSERT INTO patent_documents(
                        document_id, publication_number, title, language, url,
                        abstract_text, claims_text, description_text, content_hash,
                        metadata_json, created_at, updated_at
                    ) VALUES (?, ?, ?, 'en', ?, ?, ?, ?, ?, '{}', ?, ?)
                    """,
                    (
                        document_id,
                        publication_number,
                        "Historical integration patent",
                        f"https://example.test/{publication_number}",
                        abstract,
                        claims,
                        description,
                        content_hash,
                        1_784_592_000_000,
                        1_784_592_000_000,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO run_documents(run_id, document_id, deep_reviewed)
                    VALUES (?, ?, 1)
                    """,
                    (run["run_id"], document_id),
                )

            dsn = os.environ["AIFPATENT_POSTGRES_DSN"]
            objects = self._objects()
            ingest = self._ingest(database, dsn, objects)
            migrator = HistoricalCorpusMigrator(database=database, ingest=ingest)
            try:
                dry_run = await migrator.migrate(apply=False, run_id=run["run_id"])
                applied = await migrator.migrate(apply=True, run_id=run["run_id"])
                repeated = await migrator.migrate(apply=True, run_id=run["run_id"])

                self.assertEqual(dry_run.outcomes[0].status, MigrationStatus.LOCAL_READY)
                self.assertEqual(applied.outcomes[0].status, MigrationStatus.MIGRATED_LOCAL)
                self.assertEqual(repeated.outcomes[0].status, MigrationStatus.ALREADY_READY)
                self.assertEqual(
                    applied.outcomes[0].version_id,
                    repeated.outcomes[0].version_id,
                )
                await self._assert_postgres_state(
                    dsn,
                    run["run_id"],
                    document_id,
                    applied.outcomes[0].version_id,
                    expected_chunk_count=3,
                )
            finally:
                await self._cleanup(
                    dsn,
                    case["case_id"],
                    document_id,
                    publication_number,
                    objects,
                )

    def _objects(self) -> S3ObjectStore:
        return S3ObjectStore(
            bucket=os.environ["AIFPATENT_S3_BUCKET"],
            endpoint_url=os.environ["AIFPATENT_S3_ENDPOINT_URL"],
            access_key=os.environ["AIFPATENT_S3_ACCESS_KEY"],
            secret_key=os.environ["AIFPATENT_S3_SECRET_KEY"],
            region_name=os.environ.get("AIFPATENT_S3_REGION", "us-east-1"),
        )

    def _ingest(
        self, database: Database, dsn: str, objects: S3ObjectStore
    ) -> PatentCorpusIngestService:
        corpus = PatentCorpusService(
            versions=PostgreSQLCorpusVersionRepository(dsn),
            objects=objects,
            sources=PostgreSQLCorpusVersionSourceRepository(dsn),
            corpus_prefix="integration-corpus",
        )
        return PatentCorpusIngestService(
            corpus=corpus,
            run_links=PostgreSQLCorpusRunLinkRepository(dsn),
            prerequisites=PostgreSQLCorpusPrerequisiteRepository(database, dsn),
            chunk_persistence=PatentChunkPersistenceService(
                repository=PostgreSQLPatentChunkRepository(dsn)
            ),
        )

    async def _run_scenario(self) -> None:
        required = (
            "AIFPATENT_POSTGRES_DSN",
            "AIFPATENT_S3_ENDPOINT_URL",
            "AIFPATENT_S3_BUCKET",
            "AIFPATENT_S3_ACCESS_KEY",
            "AIFPATENT_S3_SECRET_KEY",
        )
        missing = [name for name in required if not os.environ.get(name)]
        self.assertEqual(missing, [], "integration environment is incomplete")

        suffix = uuid.uuid4().hex
        document_id = f"integration-doc-{suffix}"
        publication_number = f"INTEGRATION-{suffix}"
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "idea.sqlite3")
            database.initialize()
            case = database.create_case(f"Corpus integration {suffix}")
            run = database.create_run(
                case_id=case["case_id"],
                input_text="Verify durable corpus ingestion.",
                evaluation_date="2026-07-21",
                date_basis="publication",
                analysis_scope="integration",
                model="fixture",
                skill_version="integration/1",
                workflow_version="integration/1",
                config_snapshot={},
            )
            with database.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO patent_documents(
                        document_id, publication_number, title, language, url,
                        abstract_text, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        document_id,
                        publication_number,
                        "Integration patent",
                        "en",
                        "https://example.test/integration",
                        (
                            "Durable corpus integration content with extensive patent "
                            "context and implementation details. 缓存淘汰方法通过热度阈值"
                            "选择数据块并降低缓存未命中率。"
                        ),
                        1_784_592_000_000,
                        1_784_592_000_000,
                    ),
                )
                connection.execute(
                    "INSERT INTO run_documents(run_id, document_id) VALUES (?, ?)",
                    (run["run_id"], document_id),
                )

            dsn = os.environ["AIFPATENT_POSTGRES_DSN"]
            objects = S3ObjectStore(
                bucket=os.environ["AIFPATENT_S3_BUCKET"],
                endpoint_url=os.environ["AIFPATENT_S3_ENDPOINT_URL"],
                access_key=os.environ["AIFPATENT_S3_ACCESS_KEY"],
                secret_key=os.environ["AIFPATENT_S3_SECRET_KEY"],
                region_name=os.environ.get("AIFPATENT_S3_REGION", "us-east-1"),
            )
            corpus = PatentCorpusService(
                versions=PostgreSQLCorpusVersionRepository(dsn),
                objects=objects,
                sources=PostgreSQLCorpusVersionSourceRepository(dsn),
                corpus_prefix="integration-corpus",
            )
            ingest = PatentCorpusIngestService(
                corpus=corpus,
                run_links=PostgreSQLCorpusRunLinkRepository(dsn),
                prerequisites=PostgreSQLCorpusPrerequisiteRepository(database, dsn),
                chunk_persistence=PatentChunkPersistenceService(
                    repository=PostgreSQLPatentChunkRepository(dsn)
                ),
            )
            document = FetchedDocument(
                provider="integration-fixture",
                publication_number=publication_number,
                language="en",
                url="https://example.test/integration",
                title="Integration patent",
                abstract_text=(
                    "Durable corpus integration content with extensive patent "
                    "context and implementation details. 缓存淘汰方法通过热度阈值"
                    "选择数据块并降低缓存未命中率。"
                ),
                raw_metadata={"fixture": True},
            )
            try:
                first = await ingest.ingest_many(
                    run_id=run["run_id"],
                    documents=(document,),
                    document_ids={publication_number: document_id},
                )
                second = await ingest.ingest_many(
                    run_id=run["run_id"],
                    documents=(document,),
                    document_ids={publication_number: document_id},
                )
                self.assertEqual(first, second)
                version_id = first.version_ids[0]
                version = await corpus.get_ready(version_id)
                self.assertTrue(await objects.get(version.object_key))

                lexical = PostgreSQLLexicalSearchRepository(dsn)
                self.assertEqual(await lexical.repair((version_id,)), 1)
                hits = await lexical.search(
                    LexicalSearchRequest(
                        query_id="integration-query",
                        text="durable corpus integration",
                        allowed_version_ids=(version_id,),
                    )
                )
                self.assertTrue(hits)
                self.assertTrue(all(hit.chunk.version_id == version_id for hit in hits))
                chinese_hits = await lexical.search(
                    LexicalSearchRequest(
                        query_id="integration-query-zh",
                        text="缓存淘汰方法",
                        allowed_version_ids=(version_id,),
                    )
                )
                typo_hits = await lexical.search(
                    LexicalSearchRequest(
                        query_id="integration-query-typo",
                        text="durabl corpus integrtion",
                        allowed_version_ids=(version_id,),
                    )
                )
                excluded = await lexical.search(
                    LexicalSearchRequest(
                        query_id="integration-query-section",
                        text="durable corpus",
                        allowed_version_ids=(version_id,),
                        section_types=("claims",),
                    )
                )
                self.assertTrue(chinese_hits)
                self.assertTrue(typo_hits)
                self.assertEqual(excluded, ())

                stale_chunk_id = f"integration-stale-{suffix}"
                await self._insert_stale_chunk(
                    dsn,
                    stale_chunk_id,
                    version_id,
                    publication_number,
                )
                with self.assertRaisesRegex(
                    PostgreSQLCorpusError,
                    "conflict with deterministic output",
                ):
                    await ingest.ingest_many(
                        run_id=run["run_id"],
                        documents=(document,),
                        document_ids={publication_number: document_id},
                    )
                await self._delete_chunk(dsn, stale_chunk_id)

                await ingest.mark_deep_reviewed(run["run_id"], (document_id,))
                await self._assert_postgres_state(dsn, run["run_id"], document_id, version_id)
            finally:
                active_error = sys.exc_info()[1]
                try:
                    await self._cleanup(
                        dsn,
                        case["case_id"],
                        document_id,
                        publication_number,
                        objects,
                    )
                except Exception as cleanup_error:
                    if active_error is None:
                        raise
                    active_error.add_note(f"integration cleanup also failed: {cleanup_error}")

    async def _assert_postgres_state(
        self,
        dsn: str,
        run_id: str,
        document_id: str,
        version_id: str,
        *,
        expected_chunk_count: int = 1,
    ) -> None:
        import psycopg

        connection = await psycopg.AsyncConnection.connect(dsn)
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT rdv.deep_reviewed, rd.deep_reviewed,
                           pd.abstract_text IS NULL
                               AND pd.claims_text IS NULL
                               AND pd.description_text IS NULL,
                           (SELECT COUNT(*) FROM patent_version_sources WHERE version_id = %s),
                           (SELECT COUNT(*) FROM patent_chunks WHERE version_id = %s)
                    FROM run_document_versions AS rdv
                    JOIN run_documents AS rd USING (run_id, document_id)
                    JOIN patent_documents AS pd USING (document_id)
                    WHERE rdv.run_id = %s AND rdv.document_id = %s
                          AND rdv.version_id = %s
                    """,
                    (version_id, version_id, run_id, document_id, version_id),
                )
                row = await cursor.fetchone()
            self.assertEqual(row, (True, True, True, 1, expected_chunk_count))
        finally:
            await connection.close()

    async def _insert_stale_chunk(
        self,
        dsn: str,
        chunk_id: str,
        version_id: str,
        publication_number: str,
    ) -> None:
        import psycopg

        connection = await psycopg.AsyncConnection.connect(dsn)
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO patent_chunks(
                        chunk_id, version_id, publication_number, section_type,
                        section_label, parent_claims_json, start_offset, end_offset,
                        text, text_hash, token_count, chunker_version,
                        metadata_json, created_at
                    ) VALUES (
                        %s, %s, %s, 'abstract', 'stale', '[]'::jsonb, 0, 5,
                        'stale', %s, 1, 'claims-paragraphs-v1', '{}'::jsonb, 1
                    )
                    """,
                    (chunk_id, version_id, publication_number, "c" * 64),
                )
            await connection.commit()
        finally:
            await connection.close()

    async def _delete_chunk(self, dsn: str, chunk_id: str) -> None:
        import psycopg

        connection = await psycopg.AsyncConnection.connect(dsn)
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "DELETE FROM patent_chunks WHERE chunk_id = %s",
                    (chunk_id,),
                )
            await connection.commit()
        finally:
            await connection.close()

    async def _cleanup(
        self,
        dsn: str,
        case_id: str,
        document_id: str,
        publication_number: str,
        objects: S3ObjectStore,
    ) -> None:
        import psycopg

        object_keys: set[str] = set()
        blob_hashes: set[str] = set()
        cleanup_errors: list[Exception] = []
        try:
            connection = await psycopg.AsyncConnection.connect(dsn)
            try:
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        SELECT cb.blob_hash, cb.object_key
                        FROM patent_document_versions AS pv
                        JOIN corpus_blobs AS cb ON cb.blob_hash = pv.normalized_blob_hash
                        WHERE pv.document_id = %s
                        """,
                        (document_id,),
                    )
                    for blob_hash, object_key in await cursor.fetchall():
                        blob_hashes.add(str(blob_hash))
                        object_keys.add(str(object_key))
                    await cursor.execute(
                        """
                        DELETE FROM patent_version_sources
                        WHERE version_id IN (
                            SELECT version_id FROM patent_document_versions
                            WHERE document_id = %s
                        )
                        """,
                        (document_id,),
                    )
                    await cursor.execute(
                        "DELETE FROM run_document_versions WHERE document_id = %s",
                        (document_id,),
                    )
                    await cursor.execute(
                        """
                        DELETE FROM patent_chunks WHERE version_id IN (
                            SELECT version_id FROM patent_document_versions
                            WHERE document_id = %s
                        )
                        """,
                        (document_id,),
                    )
                    await cursor.execute(
                        "DELETE FROM patent_document_versions WHERE document_id = %s",
                        (document_id,),
                    )
                    for blob_hash in blob_hashes:
                        await cursor.execute(
                            "DELETE FROM corpus_blobs WHERE blob_hash = %s",
                            (blob_hash,),
                        )
                    await cursor.execute(
                        "DELETE FROM idea_cases WHERE case_id = %s",
                        (case_id,),
                    )
                    await cursor.execute(
                        "DELETE FROM patent_documents WHERE document_id = %s",
                        (document_id,),
                    )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
            finally:
                await connection.close()
        except Exception as exc:
            cleanup_errors.append(exc)
        try:
            client = objects._get_client()
            prefix = f"integration-corpus/{publication_number}/"
            response = await asyncio.to_thread(
                client.list_objects_v2,
                Bucket=objects.bucket,
                Prefix=prefix,
            )
            object_keys.update(str(item["Key"]) for item in response.get("Contents", []))
            for object_key in object_keys:
                await asyncio.to_thread(
                    client.delete_object,
                    Bucket=objects.bucket,
                    Key=object_key,
                )
        except Exception as exc:
            cleanup_errors.append(exc)
        if cleanup_errors:
            messages = "; ".join(str(error) for error in cleanup_errors)
            raise RuntimeError(f"integration cleanup failed: {messages}")


if __name__ == "__main__":
    unittest.main()
