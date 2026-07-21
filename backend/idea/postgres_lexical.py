from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable
from typing import Any

from .chunks import PatentChunk
from .lexical import (
    LEXICAL_TOKENIZER_VERSION,
    LexicalHit,
    LexicalSearchRequest,
    lexical_query_terms,
    lexical_search_terms,
    normalize_lexical_text,
)


Connect = Callable[[str], Awaitable[Any]]


class PostgreSQLLexicalSearchRepository:
    """Parameterised lexical retrieval constrained to explicit Corpus Versions."""

    _FIELDS = (
        "chunk_id", "version_id", "publication_number", "section_type",
        "section_label", "claim_number", "claim_kind", "parent_claims_json",
        "start_offset", "end_offset", "text", "text_hash", "token_count",
        "chunker_version", "lexical_score", "match_kind",
    )

    def __init__(self, dsn: str | None = None, *, connect: Connect | None = None) -> None:
        self.dsn = (dsn or os.environ.get("AIFPATENT_POSTGRES_DSN") or "").strip()
        if not self.dsn:
            raise ValueError("PostgreSQL lexical search requires a DSN")
        self._connect = connect

    async def _connection(self):
        if self._connect is not None:
            return await self._connect(self.dsn)
        import psycopg

        return await psycopg.AsyncConnection.connect(self.dsn)

    async def search(self, request: LexicalSearchRequest) -> tuple[LexicalHit, ...]:
        terms = lexical_query_terms(request.text)
        if not terms.value:
            raise ValueError("lexical query contains no searchable terms")
        normalized = normalize_lexical_text(request.text)
        sections = None if request.section_types is None else list(request.section_types)
        connection = await self._connection()
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    WITH query_input AS (
                        SELECT plainto_tsquery('simple'::regconfig, %s) AS tsq,
                               %s::text AS needle
                    )
                    SELECT c.chunk_id, c.version_id, c.publication_number,
                           c.section_type, c.section_label, c.claim_number,
                           c.claim_kind, c.parent_claims_json, c.start_offset,
                           c.end_offset, c.text, c.text_hash, c.token_count,
                           c.chunker_version,
                           (ts_rank_cd(c.search_tsv, q.tsq)
                              + word_similarity(q.needle, c.search_text)) AS lexical_score,
                           CASE WHEN position(q.needle in c.search_text) > 0
                                THEN 'exact' WHEN c.search_tsv @@ q.tsq
                                THEN 'fts' ELSE 'trigram' END AS match_kind
                    FROM patent_chunks AS c CROSS JOIN query_input AS q
                    WHERE c.version_id = ANY(%s)
                      AND (%s::text[] IS NULL OR c.section_type = ANY(%s))
                      AND c.metadata_json->>'lexical_tokenizer_version' = %s
                      AND (c.search_tsv @@ q.tsq OR c.search_text %%> q.needle)
                    ORDER BY (position(q.needle in c.search_text) > 0) DESC,
                             lexical_score DESC, c.chunk_id ASC
                    LIMIT %s
                    """,
                    (
                        terms.value,
                        normalized,
                        list(request.allowed_version_ids),
                        sections,
                        sections,
                        LEXICAL_TOKENIZER_VERSION,
                        request.limit,
                    ),
                )
                rows = await cursor.fetchall()
        finally:
            await connection.close()
        hits = []
        for rank, row in enumerate(rows, 1):
            values = row if isinstance(row, dict) else dict(zip(self._FIELDS, row, strict=True))
            parents = values["parent_claims_json"] or []
            if isinstance(parents, str):
                parents = json.loads(parents)
            chunk = PatentChunk(
                chunk_id=str(values["chunk_id"]),
                version_id=str(values["version_id"]),
                publication_number=str(values["publication_number"]),
                section_type=str(values["section_type"]),
                section_label=str(values["section_label"]),
                claim_number=None if values["claim_number"] is None else int(values["claim_number"]),
                claim_kind=None if values["claim_kind"] is None else str(values["claim_kind"]),
                parent_claim_numbers=tuple(int(value) for value in parents),
                start_offset=int(values["start_offset"]),
                end_offset=int(values["end_offset"]),
                text=str(values["text"]),
                text_hash=str(values["text_hash"]),
                token_count=int(values["token_count"]),
                chunker_version=str(values["chunker_version"]),
            )
            hits.append(
                LexicalHit(
                    query_id=request.query_id,
                    rank=rank,
                    lexical_score=float(values["lexical_score"]),
                    match_kind=str(values["match_kind"]),
                    chunk=chunk,
                )
            )
        return tuple(hits)

    async def repair(self, version_ids: tuple[str, ...]) -> int:
        if (
            not version_ids
            or len(set(version_ids)) != len(version_ids)
            or any(not value.strip() for value in version_ids)
        ):
            raise ValueError("lexical repair requires unique non-empty Version IDs")
        connection = await self._connection()
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT chunk_id, version_id, publication_number, section_type,
                           section_label, text, metadata_json
                    FROM patent_chunks WHERE version_id = ANY(%s)
                    ORDER BY chunk_id
                    """,
                    (list(version_ids),),
                )
                rows = await cursor.fetchall()
                row_fields = (
                    "chunk_id", "version_id", "publication_number", "section_type",
                    "section_label", "text", "metadata_json",
                )
                mapped_rows = [
                    row if isinstance(row, dict) else dict(zip(row_fields, row, strict=True))
                    for row in rows
                ]
                found_versions = {str(row["version_id"]) for row in mapped_rows}
                if found_versions != set(version_ids):
                    raise RuntimeError("lexical repair Version scope is incomplete")
                for values in mapped_rows:
                    terms = lexical_search_terms(
                        text=str(values["text"]),
                        publication_number=str(values["publication_number"]),
                        section_type=str(values["section_type"]),
                        section_label=str(values["section_label"]),
                    )
                    metadata = values["metadata_json"] or {}
                    if isinstance(metadata, str):
                        metadata = json.loads(metadata)
                    metadata = {
                        **metadata,
                        "lexical_tokenizer_version": terms.version,
                    }
                    await cursor.execute(
                        """
                        UPDATE patent_chunks
                        SET search_terms = %s, metadata_json = %s::jsonb
                        WHERE chunk_id = %s
                        """,
                        (terms.value, json.dumps(metadata), values["chunk_id"]),
                    )
                await cursor.execute(
                    """
                    SELECT COUNT(*) FROM patent_chunks
                    WHERE version_id = ANY(%s)
                      AND (search_terms = '' OR
                           metadata_json->>'lexical_tokenizer_version'
                               IS DISTINCT FROM %s)
                    """,
                    (list(version_ids), LEXICAL_TOKENIZER_VERSION),
                )
                row = await cursor.fetchone()
                stale = int(row[0] if not isinstance(row, dict) else row["count"])
                if stale:
                    raise RuntimeError("lexical index repair verification failed")
            await connection.commit()
            return len(rows)
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()


__all__ = ["PostgreSQLLexicalSearchRepository"]
