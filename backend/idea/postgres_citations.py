from __future__ import annotations

import json
import os
import re
from collections.abc import Awaitable, Callable
from typing import Any

from .citations import (
    CitationVerificationError,
    CitationVerifier,
    ModelCitationSelection,
    VerifiedCitation,
)
from .context import AssembledModelContext
from .database import now_ms


Connect = Callable[[str], Awaitable[Any]]


class PostgreSQLCitationRepository:
    _FIELDS = (
        "feature_id", "context_id", "binding_json", "chunk_id", "version_id",
        "publication_number", "section_type", "section_label", "claim_number",
        "start_offset", "end_offset", "text", "text_hash", "corpus_snapshot_hash",
        "prompt_version", "retriever_version", "context_hash",
        "corpus_availability", "deep_reviewed", "version_state",
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

    async def record(
        self,
        *,
        run_id: str,
        document_id: str,
        context: AssembledModelContext,
        selections: tuple[ModelCitationSelection, ...],
    ) -> None:
        if context.run_id != run_id or context.purpose != "INITIAL_REVIEW":
            raise CitationVerificationError("model Citation escaped its Context Run/purpose")
        bindings = {
            str(item["alias"]): str(item["chunk_id"])
            for item in context.selected_chunks
        }
        seen: set[tuple[str, str, str]] = set()
        for selection in selections:
            if re.fullmatch(r"F[1-9][0-9]*", selection.feature_id) is None:
                raise CitationVerificationError("model Citation feature identity is invalid")
            if bindings.get(selection.alias) != selection.chunk_id:
                raise CitationVerificationError(
                    "model Citation alias/chunk does not match its Context"
                )
            key = (selection.feature_id, selection.alias, selection.chunk_id)
            if key in seen:
                raise CitationVerificationError("duplicate model Citation selection")
            seen.add(key)
        connection = await self._connection()
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    DELETE FROM report_model_citations
                    WHERE run_id = %s AND document_id = %s AND context_id = %s
                    """,
                    (run_id, document_id, context.context_id),
                )
                for selection in selections:
                    await cursor.execute(
                        """
                        INSERT INTO report_model_citations(
                            run_id, document_id, feature_id, context_id,
                            alias, chunk_id, created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (
                            run_id, document_id, feature_id, context_id, alias, chunk_id
                        ) DO NOTHING
                        """,
                        (
                            run_id, document_id, f"{run_id}:{selection.feature_id}",
                            context.context_id, selection.alias, selection.chunk_id, now_ms(),
                        ),
                    )
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def for_run(self, run_id: str) -> tuple[VerifiedCitation, ...]:
        connection = await self._connection()
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT mc.feature_id, m.context_id, binding.value AS binding_json,
                           c.chunk_id, c.version_id, c.publication_number,
                           c.section_type, c.section_label, c.claim_number,
                           c.start_offset, c.end_offset, c.text, c.text_hash,
                           m.corpus_snapshot_hash, m.prompt_version,
                           m.retriever_version, m.context_hash,
                           rdv.corpus_availability, rdv.deep_reviewed,
                           pv.state AS version_state
                    FROM report_model_citations AS mc
                    JOIN patent_chunks AS c ON c.chunk_id = mc.chunk_id
                    JOIN run_document_versions AS rdv
                      ON rdv.run_id = mc.run_id
                     AND rdv.document_id = mc.document_id
                     AND rdv.version_id = c.version_id
                    JOIN patent_document_versions AS pv
                      ON pv.version_id = c.version_id
                    JOIN model_context_manifests AS m
                      ON m.context_id = mc.context_id
                     AND m.run_id = mc.run_id
                     AND m.purpose = 'INITIAL_REVIEW'
                    CROSS JOIN LATERAL jsonb_array_elements(
                        m.citation_bindings_json
                    ) AS binding(value)
                    WHERE mc.run_id = %s
                      AND binding.value->>'alias' = mc.alias
                      AND binding.value->>'chunk_id' = mc.chunk_id
                    ORDER BY mc.feature_id, c.publication_number,
                             m.context_id, mc.alias, c.chunk_id
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
            chunk["claim_number"] = (
                None if chunk["claim_number"] is None else int(chunk["claim_number"])
            )
            if (
                row["corpus_availability"] != "READY"
                or row["deep_reviewed"] is not True
                or row["version_state"] != "READY"
            ):
                raise CitationVerificationError(
                    "Citation Version is not READY and deep-reviewed in its Run scope"
                )
            citation = CitationVerifier.verify(
                binding,
                chunk,
                context_provenance={
                    "corpus_snapshot_hash": row["corpus_snapshot_hash"],
                    "prompt_version": row["prompt_version"],
                    "retriever_version": row["retriever_version"],
                    "context_hash": row["context_hash"],
                },
            )
            key = (citation.feature_id, citation.context_id, citation.chunk_id)
            if key not in seen:
                seen.add(key)
                citations.append(citation)
        return tuple(citations)

    async def context_provenance_for_run(
        self, run_id: str
    ) -> tuple[dict[str, str], ...]:
        connection = await self._connection()
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT context_id, corpus_snapshot_hash, prompt_version,
                           retriever_version, context_hash
                    FROM model_context_manifests
                    WHERE run_id = %s AND purpose = 'INITIAL_REVIEW'
                    ORDER BY context_id
                    """,
                    (run_id,),
                )
                rows = await cursor.fetchall()
        finally:
            await connection.close()
        fields = (
            "context_id", "corpus_snapshot_hash", "prompt_version",
            "retriever_version", "context_hash",
        )
        result = []
        for raw in rows:
            row = raw if isinstance(raw, dict) else dict(zip(fields, raw, strict=True))
            item = {field: str(row[field]) for field in fields}
            if not item["context_id"].startswith("CTX-"):
                raise CitationVerificationError("Context provenance identity is invalid")
            if re.fullmatch(r"[0-9a-f]{64}", item["corpus_snapshot_hash"]) is None:
                raise CitationVerificationError("Context Corpus snapshot hash is invalid")
            if re.fullmatch(r"[0-9a-f]{64}", item["context_hash"]) is None:
                raise CitationVerificationError("Context hash is invalid")
            if not item["prompt_version"] or not item["retriever_version"]:
                raise CitationVerificationError("Context provenance versions are missing")
            result.append(item)
        return tuple(result)


__all__ = ["PostgreSQLCitationRepository"]
