from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable
from typing import Any

from .citations import CitationVerificationError, CitationVerifier, VerifiedCitation


Connect = Callable[[str], Awaitable[Any]]


class PostgreSQLCitationRepository:
    _FIELDS = (
        "feature_id", "context_id", "binding_json", "chunk_id", "version_id",
        "publication_number", "section_type", "section_label", "claim_number",
        "start_offset", "end_offset", "text", "text_hash",
    )

    def __init__(self, dsn: str | None = None, *, connect: Connect | None = None) -> None:
        self.dsn = (dsn or os.environ.get("AIFPATENT_POSTGRES_DSN") or "").strip()
        if not self.dsn:
            raise ValueError("PostgreSQL Citation repository requires a DSN")
        self._connect = connect

    async def _connection(self) -> Any:
        if self._connect is not None:
            return await self._connect(self.dsn)
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover
            raise CitationVerificationError("psycopg is required for Citation loading") from exc
        return await psycopg.AsyncConnection.connect(self.dsn)

    async def for_run(self, run_id: str) -> tuple[VerifiedCitation, ...]:
        connection = await self._connection()
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT h.feature_id, m.context_id, binding.value AS binding_json,
                           c.chunk_id, c.version_id, c.publication_number,
                           c.section_type, c.section_label, c.claim_number,
                           c.start_offset, c.end_offset, c.text, c.text_hash
                    FROM report_retrieval_hits AS h
                    JOIN patent_chunks AS c ON c.chunk_id = h.chunk_id
                    JOIN model_context_manifests AS m
                      ON m.run_id = h.run_id AND m.purpose = 'INITIAL_REVIEW'
                    CROSS JOIN LATERAL jsonb_array_elements(
                        m.citation_bindings_json
                    ) AS binding(value)
                    WHERE h.run_id = %s
                      AND h.selected_for_context = TRUE
                      AND binding.value->>'chunk_id' = h.chunk_id
                      AND binding.value->>'version_id' = h.version_id
                    ORDER BY h.feature_id, c.publication_number,
                             m.context_id, binding.value->>'alias', c.chunk_id
                    """,
                    (run_id,),
                )
                rows = await cursor.fetchall()
        finally:
            await connection.close()

        citations = []
        seen = set()
        prefix = f"{run_id}:"
        for raw in rows:
            row = raw if isinstance(raw, dict) else dict(zip(self._FIELDS, raw, strict=True))
            stored_feature = str(row["feature_id"])
            if not stored_feature.startswith(prefix):
                raise CitationVerificationError("Citation feature escaped its Run scope")
            binding = row["binding_json"] or {}
            if isinstance(binding, str):
                binding = json.loads(binding)
            binding = dict(binding)
            binding["context_id"] = str(row["context_id"])
            binding["feature_id"] = stored_feature[len(prefix):]
            chunk = {field: row[field] for field in (
                "chunk_id", "version_id", "publication_number", "section_type",
                "section_label", "claim_number", "start_offset", "end_offset",
                "text", "text_hash",
            )}
            citation = CitationVerifier.verify(binding, chunk)
            key = (citation.feature_id, citation.context_id, citation.chunk_id)
            if key not in seen:
                seen.add(key)
                citations.append(citation)
        return tuple(citations)


__all__ = ["PostgreSQLCitationRepository"]
