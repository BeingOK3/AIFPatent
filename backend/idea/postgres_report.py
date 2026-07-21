from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from typing import Any, Sequence

from .agent_schemas import IdeaFeature
from .database import now_ms
from .report_retrieval import (
    ReportDocumentScope,
    ReportRetrievalError,
    ReportRetrievalResult,
)


Connect = Callable[[str], Awaitable[Any]]


class PostgreSQLReportScopeRepository:
    """Stores the frozen lexical matrix and its Chunk-level audit trail."""

    def __init__(self, dsn: str | None = None, *, connect: Connect | None = None) -> None:
        self.dsn = (dsn or os.environ.get("AIFPATENT_POSTGRES_DSN") or "").strip()
        if not self.dsn:
            raise ValueError("PostgreSQL report repository requires a DSN")
        self._connect = connect

    async def _connection(self) -> Any:
        if self._connect is not None:
            return await self._connect(self.dsn)
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise ReportRetrievalError("psycopg is required for report retrieval") from exc
        return await psycopg.AsyncConnection.connect(self.dsn)

    async def prepare_features(
        self, run_id: str, features: tuple[IdeaFeature, ...]
    ) -> None:
        connection = await self._connection()
        try:
            async with connection.cursor() as cursor:
                for ordinal, feature in enumerate(features, start=1):
                    source_start = feature.source_span.start if feature.source_span else None
                    source_end = feature.source_span.end if feature.source_span else None
                    await cursor.execute(
                        """
                        INSERT INTO idea_features(
                            feature_id, run_id, ordinal, feature_text, source_type,
                            source_start, source_end, metadata_json
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, '{}'::jsonb)
                        ON CONFLICT (feature_id) DO NOTHING
                        """,
                        (
                            f"{run_id}:{feature.feature_id}", run_id, ordinal,
                            feature.feature_text, feature.source_type, source_start, source_end,
                        ),
                    )
                await cursor.execute(
                    "SELECT COUNT(*) FROM idea_features WHERE run_id = %s",
                    (run_id,),
                )
                row = await cursor.fetchone()
                count = int(row[0] if not isinstance(row, dict) else next(iter(row.values())))
                if count != len(features):
                    raise ReportRetrievalError("PostgreSQL report feature scope is incomplete")
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def load_ready_deep_reviewed(
        self, run_id: str
    ) -> tuple[ReportDocumentScope, ...]:
        connection = await self._connection()
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT rdv.document_id, rdv.version_id, pd.publication_number
                    FROM run_document_versions AS rdv
                    JOIN patent_document_versions AS pv ON pv.version_id = rdv.version_id
                    JOIN patent_documents AS pd ON pd.document_id = rdv.document_id
                    WHERE rdv.run_id = %s
                      AND rdv.corpus_availability = 'READY'
                      AND rdv.deep_reviewed = TRUE
                      AND pv.state = 'READY'
                    ORDER BY pd.publication_number, rdv.document_id, rdv.version_id
                    """,
                    (run_id,),
                )
                rows = await cursor.fetchall()
            return tuple(
                ReportDocumentScope(
                    document_id=str(row["document_id"] if isinstance(row, dict) else row[0]),
                    version_id=str(row["version_id"] if isinstance(row, dict) else row[1]),
                    publication_number=str(
                        row["publication_number"] if isinstance(row, dict) else row[2]
                    ),
                )
                for row in rows
            )
        finally:
            await connection.close()

    async def persist(self, result: ReportRetrievalResult) -> None:
        pairs = [(item.feature_id, item.version_id) for item in result.queries]
        if len(pairs) != len(set(pairs)):
            raise ReportRetrievalError("retrieval result contains duplicate Feature × Version pairs")
        connection = await self._connection()
        try:
            timestamp = now_ms()
            async with connection.cursor() as cursor:
                for query in result.queries:
                    await cursor.execute(
                        """
                        INSERT INTO report_retrieval_queries(
                            query_id, run_id, document_id, version_id, feature_id,
                            publication_number, query_text, hit_count,
                            retriever_version, created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (query_id) DO NOTHING
                        """,
                        (
                            query.query_id, result.run_id, query.document_id,
                            query.version_id, f"{result.run_id}:{query.feature_id}",
                            query.publication_number, query.feature_text, query.hit_count,
                            result.retriever_version, timestamp,
                        ),
                    )
                for selection in result.selections:
                    await cursor.execute(
                        """
                        INSERT INTO report_retrieval_hits(
                            run_id, version_id, feature_id, chunk_id, selection_reason,
                            lexical_rank, selected_for_context, retriever_version, created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (
                            run_id, version_id, feature_id, chunk_id, selection_reason
                        ) DO NOTHING
                        """,
                        (
                            result.run_id, selection.version_id,
                            f"{result.run_id}:{selection.feature_id}",
                            selection.hit.chunk.chunk_id, selection.selection_reason,
                            selection.hit.rank, selection.selected_for_context,
                            result.retriever_version, timestamp,
                        ),
                    )
                await cursor.execute(
                    "SELECT COUNT(*) FROM report_retrieval_queries WHERE run_id = %s AND retriever_version = %s",
                    (result.run_id, result.retriever_version),
                )
                row = await cursor.fetchone()
                count = int(row[0] if not isinstance(row, dict) else next(iter(row.values())))
                if count != len(result.queries):
                    raise ReportRetrievalError("persisted Feature × Version matrix is incomplete")
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()


__all__ = ["PostgreSQLReportScopeRepository"]
