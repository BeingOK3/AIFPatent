from __future__ import annotations

import json
import math
import os
from collections.abc import Awaitable, Callable
from typing import Any

from .chunks import PatentChunk
from .embeddings import EmbeddingProfile
from .vector import VectorHit, VectorSearchError, VectorSearchRequest


Connect = Callable[[str], Awaitable[Any]]


def _vector_literal(values: tuple[float, ...]) -> str:
    return "[" + ",".join(format(value, ".17g") for value in values) + "]"


class PgVectorIndex:
    """Exact cosine retrieval with mandatory Profile and Version filters."""

    def __init__(
        self,
        profile: EmbeddingProfile,
        dsn: str | None = None,
        *,
        connect: Connect | None = None,
    ) -> None:
        self.profile = profile
        self.dsn = (dsn or os.environ.get("AIFPATENT_POSTGRES_DSN") or "").strip()
        if not self.dsn:
            raise ValueError("PgVectorIndex requires a PostgreSQL DSN")
        self._connect = connect

    async def _connection(self) -> Any:
        if self._connect is not None:
            return await self._connect(self.dsn)
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover
            raise VectorSearchError("psycopg is required for pgvector search") from exc
        return await psycopg.AsyncConnection.connect(self.dsn)

    async def search(self, request: VectorSearchRequest) -> tuple[VectorHit, ...]:
        if request.profile_id != self.profile.profile_id:
            raise VectorSearchError("vector query profile does not match the active index")
        if len(request.embedding) != self.profile.dimensions:
            raise VectorSearchError("vector query dimensions do not match the active profile")
        norm = math.sqrt(sum(value * value for value in request.embedding))
        if not math.isclose(norm, 1.0, rel_tol=1e-5, abs_tol=1e-5):
            raise VectorSearchError("vector query must be L2-normalized")

        connection = await self._connection()
        try:
            async with connection.cursor() as cursor:
                await cursor.execute("SET LOCAL enable_indexscan = off")
                await cursor.execute("SET LOCAL enable_bitmapscan = off")
                await cursor.execute(
                    """
                    WITH active_chunkers AS (
                        SELECT DISTINCT ON (version_id) version_id, chunker_version
                        FROM patent_chunks
                        WHERE version_id = ANY(%s)
                        ORDER BY version_id, created_at DESC, chunker_version DESC
                    ), scoped AS MATERIALIZED (
                        SELECT
                            c.chunk_id, c.version_id, c.publication_number,
                            c.section_type, c.section_label, c.claim_number,
                            c.claim_kind, c.parent_claims_json, c.start_offset,
                            c.end_offset, c.text, c.text_hash, c.token_count,
                            c.chunker_version, ev.embedding
                        FROM patent_chunks c
                        JOIN active_chunkers USING (version_id, chunker_version)
                        JOIN chunk_embeddings ce ON ce.chunk_id = c.chunk_id
                        JOIN embedding_vectors ev ON ev.embedding_id = ce.embedding_id
                        JOIN embedding_profiles ep ON ep.profile_id = ev.profile_id
                        WHERE ev.profile_id = %s
                          AND ep.state = 'ACTIVE'
                          AND ep.dimensions = %s
                          AND vector_dims(ev.embedding) = %s
                          AND (%s::text[] IS NULL OR c.section_type = ANY(%s))
                    )
                    SELECT
                        chunk_id, version_id, publication_number, section_type,
                        section_label, claim_number, claim_kind,
                        parent_claims_json, start_offset, end_offset, text,
                        text_hash, token_count, chunker_version,
                        embedding <=> %s::vector AS cosine_distance
                    FROM scoped
                    ORDER BY cosine_distance ASC, chunk_id ASC
                    LIMIT %s
                    """,
                    (
                        list(request.allowed_version_ids),
                        request.profile_id,
                        self.profile.dimensions,
                        self.profile.dimensions,
                        list(request.section_types) if request.section_types else None,
                        list(request.section_types) if request.section_types else None,
                        _vector_literal(request.embedding),
                        request.limit,
                    ),
                )
                rows = await cursor.fetchall()
        finally:
            await connection.close()

        fields = (
            "chunk_id", "version_id", "publication_number", "section_type",
            "section_label", "claim_number", "claim_kind", "parent_claims_json",
            "start_offset", "end_offset", "text", "text_hash", "token_count",
            "chunker_version", "cosine_distance",
        )
        hits: list[VectorHit] = []
        for rank, row in enumerate(rows, start=1):
            values = row if isinstance(row, dict) else dict(zip(fields, row, strict=True))
            parents = values["parent_claims_json"] or []
            if isinstance(parents, str):
                parents = json.loads(parents)
            distance = float(values["cosine_distance"])
            if not math.isfinite(distance) or not -1e-6 <= distance <= 2.000001:
                raise VectorSearchError("pgvector returned an invalid cosine distance")
            chunk = PatentChunk(
                chunk_id=str(values["chunk_id"]),
                version_id=str(values["version_id"]),
                publication_number=str(values["publication_number"]),
                section_type=str(values["section_type"]),
                section_label=str(values["section_label"]),
                claim_number=(
                    int(values["claim_number"])
                    if values["claim_number"] is not None
                    else None
                ),
                claim_kind=(
                    str(values["claim_kind"])
                    if values["claim_kind"] is not None
                    else None
                ),
                parent_claim_numbers=tuple(int(value) for value in parents),
                start_offset=int(values["start_offset"] or 0),
                end_offset=int(values["end_offset"] or 0),
                text=str(values["text"]),
                text_hash=str(values["text_hash"]),
                token_count=int(values["token_count"]),
                chunker_version=str(values["chunker_version"]),
            )
            if chunk.version_id not in request.allowed_version_ids:
                raise VectorSearchError("vector hit escaped the frozen Version scope")
            if request.section_types and chunk.section_type not in request.section_types:
                raise VectorSearchError("vector hit escaped the requested section scope")
            hits.append(
                VectorHit(
                    query_id=request.query_id,
                    rank=rank,
                    cosine_distance=max(0.0, distance),
                    index_mode="exact",
                    chunk=chunk,
                )
            )
        return tuple(hits)


__all__ = ["PgVectorIndex"]
