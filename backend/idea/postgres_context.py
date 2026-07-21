from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable
from typing import Any

from .context import AssembledModelContext, ContextAssemblyError
from .database import now_ms


Connect = Callable[[str], Awaitable[Any]]


class PostgreSQLContextRepository:
    """Append-only storage for deterministic model Context Manifests."""

    def __init__(self, dsn: str | None = None, *, connect: Connect | None = None) -> None:
        self.dsn = (dsn or os.environ.get("AIFPATENT_POSTGRES_DSN") or "").strip()
        if not self.dsn:
            raise ValueError("PostgreSQL context repository requires a DSN")
        self._connect = connect

    async def _connection(self) -> Any:
        if self._connect is not None:
            return await self._connect(self.dsn)
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover
            raise ContextAssemblyError("psycopg is required for Context persistence") from exc
        return await psycopg.AsyncConnection.connect(self.dsn)

    async def put_if_absent(
        self, context: AssembledModelContext, *, agent_name: str
    ) -> str:
        if not agent_name.strip():
            raise ContextAssemblyError("Context agent name must not be empty")
        selected = [dict(item) for item in context.selected_chunks]
        allowed_versions = list(context.allowed_version_ids)
        manifest = {
            "context_id": context.context_id,
            "context_version": context.context_version,
            "purpose": context.purpose,
            "prompt_version": context.prompt_version,
            "retriever_version": context.retriever_version,
            "run_id": context.run_id,
            "turn_id": context.turn_id,
            "corpus_snapshot_hash": context.corpus_snapshot_hash,
            "allowed_version_ids": allowed_versions,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in context.messages
            ],
            "selected_chunks": selected,
            "excluded_chunks": [dict(item) for item in context.excluded_chunks],
            "selected_notes": [dict(item) for item in context.selected_notes],
            "excluded_notes": [dict(item) for item in context.excluded_notes],
            "input_budget": context.input_budget,
            "reserved_output_tokens": context.reserved_output_tokens,
            "used_input_tokens": context.used_input_tokens,
            "limitations": list(context.limitations),
            "context_hash": context.context_hash,
        }

        def encoded(value: Any) -> str:
            return json.dumps(
                value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )

        connection = await self._connection()
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO model_context_manifests(
                        context_id, purpose, run_id, turn_id, agent_name,
                        context_version, prompt_version, retriever_version,
                        token_counter_version, corpus_snapshot_hash,
                        allowed_version_ids_json, selected_chunk_ids_json,
                        citation_bindings_json, budget_json, omissions_json,
                        limitations_json, manifest_json, context_hash, created_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb,
                        %s::jsonb, %s::jsonb, %s::jsonb, %s, %s
                    ) ON CONFLICT (context_id) DO UPDATE
                    SET context_id = EXCLUDED.context_id
                    RETURNING context_hash
                    """,
                    (
                        context.context_id, context.purpose, context.run_id,
                        context.turn_id, agent_name, context.context_version,
                        context.prompt_version, context.retriever_version,
                        "estimate-words-v1", context.corpus_snapshot_hash,
                        encoded(allowed_versions),
                        encoded([item["chunk_id"] for item in selected]),
                        encoded(selected),
                        encoded({
                            "input_budget": context.input_budget,
                            "reserved_output_tokens": context.reserved_output_tokens,
                            "used_input_tokens": context.used_input_tokens,
                        }),
                        encoded([dict(item) for item in context.excluded_chunks]),
                        encoded(list(context.limitations)), encoded(manifest),
                        context.context_hash, now_ms(),
                    ),
                )
                row = await cursor.fetchone()
                persisted_hash = str(
                    row["context_hash"] if isinstance(row, dict) else row[0]
                )
                if persisted_hash != context.context_hash:
                    raise ContextAssemblyError(
                        "stored Context ID conflicts with a different context hash"
                    )
            await connection.commit()
            return context.context_id
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()


__all__ = ["PostgreSQLContextRepository"]
