from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable
from typing import Any

from .agent_schemas import IdeaFeature, SourceSpan
from .followup import FollowupError, FollowupTurn
from .followup_handler import FollowupSourceData
from .postgres_followup import PostgreSQLFollowupRepository, _decoded


Connect = Callable[[str], Awaitable[Any]]


class PostgreSQLFollowupDataSource:
    """Load only durable source-Run and prior successful-Turn context."""

    def __init__(
        self,
        dsn: str | None = None,
        *,
        connect: Connect | None = None,
        history_limit: int = 5,
    ) -> None:
        self.dsn = (dsn or os.environ.get("AIFPATENT_POSTGRES_DSN") or "").strip()
        if not self.dsn:
            raise ValueError("PostgreSQL follow-up data source requires a DSN")
        if not 1 <= history_limit <= 5:
            raise ValueError("follow-up history limit must be between 1 and 5")
        self._connect = connect
        self.history_limit = history_limit

    async def _connection(self) -> Any:
        if self._connect is not None:
            return await self._connect(self.dsn)
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover
            raise FollowupError("psycopg is required for follow-up source data") from exc
        return await psycopg.AsyncConnection.connect(self.dsn, row_factory=dict_row)

    async def load(self, *, run_id: str, turn: FollowupTurn) -> FollowupSourceData:
        connection = await self._connection()
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT ft.run_id
                    FROM followup_turns fu
                    JOIN followup_threads ft ON ft.thread_id = fu.thread_id
                    WHERE fu.turn_id = %s AND fu.thread_id = %s
                    """,
                    (turn.turn_id, turn.thread_id),
                )
                identity = await cursor.fetchone()
                actual_run = None if identity is None else str(identity["run_id"])
                if actual_run != run_id:
                    raise FollowupError("follow-up Turn does not belong to the source Run")

                await cursor.execute(
                    """
                    SELECT feature_id, ordinal, feature_text, source_type,
                           source_start, source_end, metadata_json
                    FROM idea_features
                    WHERE run_id = %s
                    ORDER BY ordinal, feature_id
                    """,
                    (run_id,),
                )
                features = tuple(self._feature(run_id, row) for row in await cursor.fetchall())
                if not features:
                    raise FollowupError("source Run has no durable IDEA Features")

                await cursor.execute(
                    """
                    SELECT * FROM followup_turns
                    WHERE thread_id = %s
                      AND created_at < %s
                      AND status IN ('COMPLETED', 'COMPLETED_WITH_LIMITATIONS')
                      AND answer_json IS NOT NULL
                    ORDER BY created_at DESC, turn_id DESC
                    LIMIT %s
                    """,
                    (turn.thread_id, turn.created_at, self.history_limit),
                )
                history_rows = await cursor.fetchall()
                recent = tuple(
                    PostgreSQLFollowupRepository._turn(row)
                    for row in reversed(history_rows)
                )

                await cursor.execute(
                    """
                    SELECT ir.status, ir.limitation_json,
                           nr.conclusion, nr.rationale AS novelty_rationale,
                           vr.result_json AS value_result
                    FROM idea_runs ir
                    LEFT JOIN novelty_results nr ON nr.run_id = ir.run_id
                    LEFT JOIN value_results vr ON vr.run_id = ir.run_id
                    WHERE ir.run_id = %s
                    """,
                    (run_id,),
                )
                report = await cursor.fetchone()
                if report is None:
                    raise FollowupError("source Run disappeared while loading follow-up data")
            summary = json.dumps(
                {
                    "run_status": str(report["status"]),
                    "novelty_conclusion": report.get("conclusion"),
                    "novelty_rationale": report.get("novelty_rationale"),
                    "value_result": _decoded(report.get("value_result")),
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            limitations_value = _decoded(report.get("limitation_json")) or []
            limitations = tuple(
                self._limitation_text(item) for item in limitations_value
            )
            return FollowupSourceData(
                features=features,
                recent_turns=recent,
                report_summary=summary,
                report_limitations=tuple(value for value in limitations if value),
            )
        finally:
            await connection.close()

    @staticmethod
    def _feature(run_id: str, row: dict[str, Any]) -> IdeaFeature:
        stored_id = str(row["feature_id"])
        prefix = run_id + ":"
        if not stored_id.startswith(prefix):
            raise FollowupError("durable IDEA Feature escaped the source Run")
        metadata = _decoded(row.get("metadata_json")) or {}
        external_id = str(metadata.get("external_feature_id") or stored_id[len(prefix) :])
        start, end = row.get("source_start"), row.get("source_end")
        if (start is None) != (end is None):
            raise FollowupError("durable IDEA Feature source span is incomplete")
        span = None
        if start is not None:
            span = SourceSpan(
                start=int(start),
                end=int(end),
                text=str(metadata.get("source_text") or row["feature_text"]),
            )
        return IdeaFeature(
            feature_id=external_id,
            feature_text=str(row["feature_text"]),
            source_type=str(row["source_type"]),
            source_span=span,
            required=bool(metadata.get("required", True)),
        )

    @staticmethod
    def _limitation_text(value: Any) -> str:
        if isinstance(value, dict):
            return str(value.get("message") or value.get("code") or "").strip()
        return str(value).strip()


__all__ = ["PostgreSQLFollowupDataSource"]
