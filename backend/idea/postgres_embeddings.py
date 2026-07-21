from __future__ import annotations

import math
import os
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from .database import now_ms
from .embeddings import CachedEmbedding, EmbeddingError, EmbeddingProfile


Connect = Callable[[str], Awaitable[Any]]


def _vector_literal(values: tuple[float, ...]) -> str:
    if not values or any(not math.isfinite(value) for value in values):
        raise EmbeddingError("cannot persist an empty or non-finite embedding")
    return "[" + ",".join(format(value, ".17g") for value in values) + "]"


def _parse_vector(value: Any) -> tuple[float, ...]:
    if isinstance(value, str):
        source = value.strip()
        if not source.startswith("[") or not source.endswith("]"):
            raise EmbeddingError("stored pgvector value is invalid")
        return tuple(float(item) for item in source[1:-1].split(",") if item)
    if isinstance(value, (list, tuple)):
        return tuple(float(item) for item in value)
    raise EmbeddingError("stored pgvector value has an unsupported type")


def _vectors_close(left: tuple[float, ...], right: tuple[float, ...]) -> bool:
    return len(left) == len(right) and all(
        math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-6)
        for a, b in zip(left, right, strict=True)
    )


class PostgreSQLEmbeddingCache:
    """Persistent text-hash cache for one versioned embedding profile."""

    def __init__(self, dsn: str | None = None, *, connect: Connect | None = None) -> None:
        self.dsn = (dsn or os.environ.get("AIFPATENT_POSTGRES_DSN") or "").strip()
        if not self.dsn:
            raise ValueError("PostgreSQL embedding cache requires a DSN")
        self._connect = connect

    async def _connection(self) -> Any:
        if self._connect is not None:
            return await self._connect(self.dsn)
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover
            raise EmbeddingError("psycopg is required for embedding persistence") from exc
        return await psycopg.AsyncConnection.connect(self.dsn)

    async def ensure_profile(self, profile: EmbeddingProfile) -> None:
        connection = await self._connection()
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO embedding_profiles(
                        profile_id, provider, model, dimensions, normalization,
                        state, created_at, activated_at
                    ) VALUES (%s, %s, %s, %s, %s, 'BUILDING', %s, NULL)
                    ON CONFLICT (profile_id) DO UPDATE
                    SET profile_id = EXCLUDED.profile_id
                    RETURNING provider, model, dimensions, normalization
                    """,
                    (
                        profile.profile_id,
                        profile.provider,
                        profile.model,
                        profile.dimensions,
                        profile.normalization,
                        now_ms(),
                    ),
                )
                row = await cursor.fetchone()
                fields = ("provider", "model", "dimensions", "normalization")
                values = row if isinstance(row, dict) else dict(zip(fields, row, strict=True))
                actual = (
                    str(values["provider"]),
                    str(values["model"]),
                    int(values["dimensions"]),
                    str(values["normalization"]),
                )
                expected = (
                    profile.provider,
                    profile.model,
                    profile.dimensions,
                    profile.normalization,
                )
                if actual != expected:
                    raise EmbeddingError("embedding profile ID conflicts with stored metadata")
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def get_many(
        self, profile_id: str, text_hashes: tuple[str, ...]
    ) -> dict[str, CachedEmbedding]:
        if not text_hashes:
            return {}
        connection = await self._connection()
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT text_hash, profile_id, embedding::text, vector_norm
                    FROM embedding_vectors
                    WHERE profile_id = %s AND text_hash = ANY(%s)
                    ORDER BY text_hash
                    """,
                    (profile_id, list(text_hashes)),
                )
                rows = await cursor.fetchall()
            result: dict[str, CachedEmbedding] = {}
            fields = ("text_hash", "profile_id", "embedding", "vector_norm")
            for row in rows:
                values = row if isinstance(row, dict) else dict(zip(fields, row, strict=True))
                record = CachedEmbedding(
                    text_hash=str(values["text_hash"]),
                    profile_id=str(values["profile_id"]),
                    embedding=_parse_vector(values["embedding"]),
                    vector_norm=float(values["vector_norm"]),
                )
                result[record.text_hash] = record
            return result
        finally:
            await connection.close()

    async def put_many(self, values: tuple[CachedEmbedding, ...]) -> None:
        if not values:
            return
        connection = await self._connection()
        try:
            async with connection.cursor() as cursor:
                for value in values:
                    embedding_id = "ev-" + uuid.uuid5(
                        uuid.NAMESPACE_URL, f"{value.profile_id}:{value.text_hash}"
                    ).hex
                    await cursor.execute(
                        """
                        INSERT INTO embedding_vectors(
                            embedding_id, text_hash, profile_id, embedding,
                            vector_norm, created_at
                        ) VALUES (%s, %s, %s, %s::vector, %s, %s)
                        ON CONFLICT (text_hash, profile_id) DO UPDATE
                        SET embedding_id = embedding_vectors.embedding_id
                        RETURNING embedding::text, vector_norm
                        """,
                        (
                            embedding_id,
                            value.text_hash,
                            value.profile_id,
                            _vector_literal(value.embedding),
                            value.vector_norm,
                            now_ms(),
                        ),
                    )
                    row = await cursor.fetchone()
                    stored_vector = _parse_vector(
                        row["embedding"] if isinstance(row, dict) else row[0]
                    )
                    stored_norm = float(
                        row["vector_norm"] if isinstance(row, dict) else row[1]
                    )
                    if not _vectors_close(stored_vector, value.embedding) or not math.isclose(
                        stored_norm, value.vector_norm, rel_tol=1e-6, abs_tol=1e-6
                    ):
                        raise EmbeddingError(
                            "stored embedding conflicts with deterministic cache value"
                        )
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def link_chunks(
        self, profile_id: str, chunk_text_hashes: tuple[tuple[str, str], ...]
    ) -> None:
        if not chunk_text_hashes:
            return
        chunk_ids = [value[0] for value in chunk_text_hashes]
        text_hashes = [value[1] for value in chunk_text_hashes]
        if len(set(chunk_ids)) != len(chunk_ids):
            raise EmbeddingError("Chunk embedding links must have unique Chunk IDs")
        connection = await self._connection()
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    WITH requested(chunk_id, text_hash) AS (
                        SELECT * FROM unnest(%s::text[], %s::text[])
                    ), inserted AS (
                        INSERT INTO chunk_embeddings(chunk_id, embedding_id)
                        SELECT c.chunk_id, ev.embedding_id
                        FROM requested r
                        JOIN patent_chunks c
                          ON c.chunk_id = r.chunk_id AND c.text_hash = r.text_hash
                        JOIN embedding_vectors ev
                          ON ev.text_hash = r.text_hash AND ev.profile_id = %s
                        ON CONFLICT (chunk_id, embedding_id) DO UPDATE
                        SET chunk_id = EXCLUDED.chunk_id
                        RETURNING chunk_id
                    )
                    SELECT chunk_id FROM inserted ORDER BY chunk_id
                    """,
                    (chunk_ids, text_hashes, profile_id),
                )
                rows = await cursor.fetchall()
                linked = {str(row["chunk_id"] if isinstance(row, dict) else row[0]) for row in rows}
                if linked != set(chunk_ids):
                    raise EmbeddingError("Chunk embedding link scope is incomplete")
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def activate_profile(
        self, profile_id: str, required_chunk_ids: tuple[str, ...]
    ) -> None:
        if not required_chunk_ids or len(set(required_chunk_ids)) != len(required_chunk_ids):
            raise EmbeddingError("profile activation requires unique Chunk IDs")
        connection = await self._connection()
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "LOCK TABLE embedding_profiles IN SHARE ROW EXCLUSIVE MODE"
                )
                await cursor.execute(
                    """
                    SELECT COUNT(DISTINCT ce.chunk_id)
                    FROM chunk_embeddings ce
                    JOIN embedding_vectors ev ON ev.embedding_id = ce.embedding_id
                    WHERE ev.profile_id = %s AND ce.chunk_id = ANY(%s)
                    """,
                    (profile_id, list(required_chunk_ids)),
                )
                row = await cursor.fetchone()
                count = int(row["count"] if isinstance(row, dict) else row[0])
                if count != len(required_chunk_ids):
                    raise EmbeddingError("embedding profile coverage is incomplete")
                await cursor.execute(
                    """
                    UPDATE embedding_profiles
                    SET state = CASE WHEN profile_id = %s THEN 'ACTIVE' ELSE 'RETIRED' END,
                        activated_at = CASE WHEN profile_id = %s THEN %s ELSE activated_at END
                    WHERE state = 'ACTIVE' OR profile_id = %s
                    RETURNING profile_id, state
                    """,
                    (profile_id, profile_id, now_ms(), profile_id),
                )
                rows = await cursor.fetchall()
                states = {
                    str(row["profile_id"] if isinstance(row, dict) else row[0]):
                    str(row["state"] if isinstance(row, dict) else row[1])
                    for row in rows
                }
                if states.get(profile_id) != "ACTIVE":
                    raise EmbeddingError("embedding profile does not exist or was not activated")
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()


__all__ = ["PostgreSQLEmbeddingCache"]
