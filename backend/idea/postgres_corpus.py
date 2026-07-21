from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from .corpus import CorpusVersion
from .ports import Repository


class PostgreSQLCorpusError(RuntimeError):
    """Raised when a PostgreSQL corpus write/read cannot be trusted."""


Connect = Callable[[str], Awaitable[Any]]


class PostgreSQLCorpusVersionRepository(Repository[str, CorpusVersion]):
    """PostgreSQL repository for immutable corpus Versions and Blob metadata."""

    def __init__(self, dsn: str | None = None, *, connect: Connect | None = None) -> None:
        self.dsn = (dsn or os.environ.get("AIFPATENT_POSTGRES_DSN") or "").strip()
        if not self.dsn:
            raise ValueError("PostgreSQL corpus repository requires a DSN")
        self._connect = connect

    async def _connection(self) -> Any:
        if self._connect is not None:
            return await self._connect(self.dsn)
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise PostgreSQLCorpusError("psycopg is required for PostgreSQL corpus storage") from exc
        return await psycopg.AsyncConnection.connect(self.dsn)

    @staticmethod
    def _millis(value: datetime) -> int:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return int(value.timestamp() * 1000)

    @staticmethod
    def _datetime(value: Any) -> datetime:
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)

    @staticmethod
    def _row_to_version(row: Any) -> CorpusVersion:
        metadata = row["metadata_json"] or {}
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        provider = str(metadata.get("provider") or "postgresql")
        return CorpusVersion(
            version_id=str(row["version_id"]),
            publication_number=str(row["publication_number"]),
            language=str(row["language"]),
            provider=provider,
            object_key=str(row["object_key"]),
            content_sha256=str(row["normalized_content_hash"]),
            normalized_size=int(row["uncompressed_bytes"]),
            created_at=PostgreSQLCorpusVersionRepository._datetime(row["created_at"]),
            status=str(row["state"]),
            document_id=str(row["document_id"]),
        )

    @staticmethod
    def _version_row_mapping(row: Any) -> dict[str, Any]:
        if isinstance(row, dict):
            return row
        return dict(
            zip(
                (
                    "version_id",
                    "document_id",
                    "publication_number",
                    "language",
                    "normalized_content_hash",
                    "state",
                    "metadata_json",
                    "created_at",
                    "object_key",
                    "uncompressed_bytes",
                ),
                row,
                strict=True,
            )
        )

    async def get(self, key: str) -> CorpusVersion | None:
        connection = await self._connection()
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT pv.version_id, pv.document_id, pd.publication_number,
                           pv.language, pv.normalized_content_hash, pv.state,
                           pv.metadata_json, pv.created_at, cb.object_key,
                           cb.uncompressed_bytes
                    FROM patent_document_versions AS pv
                    JOIN patent_documents AS pd ON pd.document_id = pv.document_id
                    JOIN corpus_blobs AS cb ON cb.blob_hash = pv.normalized_blob_hash
                    WHERE pv.version_id = %s
                    """,
                    (key,),
                )
                row = await cursor.fetchone()
            return None if row is None else self._row_to_version(self._version_row_mapping(row))
        finally:
            await connection.close()

    async def put_if_absent(self, key: str, entity: CorpusVersion) -> bool:
        if key != entity.version_id:
            raise PostgreSQLCorpusError("repository key does not match Version ID")
        connection = await self._connection()
        try:
            async with connection.cursor() as cursor:
                if entity.document_id:
                    await cursor.execute(
                        "SELECT document_id FROM patent_documents WHERE document_id = %s",
                        (entity.document_id,),
                    )
                else:
                    await cursor.execute(
                        """
                        SELECT document_id FROM patent_documents
                        WHERE publication_number = %s AND COALESCE(language, '') = %s
                        ORDER BY updated_at DESC LIMIT 1
                        """,
                        (entity.publication_number, entity.language),
                    )
                document_row = await cursor.fetchone()
                if document_row is None:
                    raise PostgreSQLCorpusError("patent document must exist before Version persistence")
                document_id = str(document_row["document_id"] if isinstance(document_row, dict) else document_row[0])
                metadata = json.dumps(
                    {"provider": entity.provider}, ensure_ascii=True, sort_keys=True, separators=(",", ":")
                )
                await cursor.execute(
                    """
                    INSERT INTO corpus_blobs(
                        blob_hash, encoding, object_key, uncompressed_bytes,
                        compressed_bytes, content_type, state, created_at, verified_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, 'READY', %s, %s)
                    ON CONFLICT (blob_hash) DO NOTHING
                    """,
                    (
                        entity.content_sha256,
                        "identity",
                        entity.object_key,
                        entity.normalized_size,
                        entity.normalized_size,
                        "application/json",
                        self._millis(entity.created_at),
                        self._millis(entity.created_at),
                    ),
                )
                await cursor.execute(
                    """
                    INSERT INTO patent_document_versions(
                        version_id, document_id, language, normalized_content_hash,
                        normalized_blob_hash, parser_version, schema_version,
                        state, metadata_json, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                    ON CONFLICT (version_id) DO NOTHING
                    """,
                    (
                        entity.version_id,
                        document_id,
                        entity.language,
                        entity.content_sha256,
                        entity.content_sha256,
                        "aifpatent-corpus/1",
                        "aifpatent-corpus/1",
                        entity.status,
                        metadata,
                        self._millis(entity.created_at),
                    ),
                )
                await connection.commit()
                return cursor.rowcount == 1
        except PostgreSQLCorpusError:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def healthcheck(self) -> dict[str, Any]:
        connection = await self._connection()
        try:
            async with connection.cursor() as cursor:
                await cursor.execute("SELECT 1")
                row = await cursor.fetchone()
            return {"ok": row is not None}
        finally:
            await connection.close()


__all__ = ["PostgreSQLCorpusError", "PostgreSQLCorpusVersionRepository"]
