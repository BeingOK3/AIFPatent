from __future__ import annotations

import time
from contextlib import contextmanager
from enum import StrEnum
from typing import Iterator

from pydantic import Field

from .scale_gate import ScaleEstimate, ScaleTier, requires_confirmation
from .scope import ScopeModel


class ScaleDecision(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ScaleGateRecord(ScopeModel):
    run_id: str = Field(pattern=r"^LRN-[0-9a-f]{16}$")
    estimate: ScaleEstimate
    decision: ScaleDecision | None = None
    created_at: int = Field(ge=0)
    decided_at: int | None = Field(default=None, ge=0)


class ScaleGatePersistenceError(RuntimeError):
    pass


class PostgreSQLScaleGateRepository:
    def __init__(self, dsn: str, *, connect=None):
        if not isinstance(dsn, str) or not dsn.strip():
            raise ValueError("PostgreSQL DSN must not be blank")
        self._dsn = dsn.strip(); self._connect_factory = connect

    def record(self, run_id: str, estimate: ScaleEstimate) -> ScaleGateRecord:
        estimate = ScaleEstimate.model_validate(estimate.model_dump(mode="json"))
        now = int(time.time() * 1000)
        auto = not requires_confirmation(estimate)
        with self._connect() as connection:
            run = connection.execute(
                "SELECT run_id,scope_revision_id,scope_revision_hash,status FROM landscape_v4_runs WHERE run_id=%s FOR UPDATE",
                (run_id,),
            ).fetchone()
            if run is None: raise KeyError(run_id)
            if run["scope_revision_id"] != estimate.scope_revision_id or run["scope_revision_hash"] != estimate.scope_revision_hash:
                raise ScaleGatePersistenceError("scale estimate does not match Run scope")
            decision = ScaleDecision.APPROVED if auto else None
            decided_at = now if auto else None
            connection.execute(
                """
                INSERT INTO landscape_v4_scale_gates(
                    run_id,scope_revision_id,scope_revision_hash,publication_start,
                    publication_end,query_count,
                    estimated_total_results,estimated_total_pages,estimated_shards,
                    tier,decision,created_at,decided_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (run_id) DO NOTHING
                """,
                (run_id, estimate.scope_revision_id, estimate.scope_revision_hash,
                 estimate.publication_start, estimate.publication_end,
                 estimate.query_count, estimate.estimated_total_results,
                 estimate.estimated_total_pages, estimate.estimated_shards,
                 estimate.tier.value, decision.value if decision else None, now, decided_at),
            )
            stored = self._load(connection, run_id)
            expected = ScaleGateRecord(run_id=run_id, estimate=estimate, decision=decision, created_at=stored.created_at, decided_at=stored.decided_at)
            if stored != expected:
                raise ScaleGatePersistenceError("Run already has a different immutable scale estimate")
            target = "READY" if auto else "AWAITING_SCALE_CONFIRMATION"
            if run["status"] == "ESTIMATING":
                connection.execute("UPDATE landscape_v4_runs SET status=%s,updated_at=%s WHERE run_id=%s AND status='ESTIMATING'", (target, now, run_id))
            elif run["status"] != target:
                raise ScaleGatePersistenceError("Run is not in a scale estimation state")
            return stored

    def decide(self, run_id: str, *, approve: bool) -> ScaleGateRecord:
        now = int(time.time() * 1000)
        decision = ScaleDecision.APPROVED if approve else ScaleDecision.REJECTED
        with self._connect() as connection:
            run = connection.execute("SELECT run_id,status FROM landscape_v4_runs WHERE run_id=%s FOR UPDATE", (run_id,)).fetchone()
            if run is None: raise KeyError(run_id)
            current = self._load(connection, run_id)
            if current.estimate.tier == ScaleTier.WITHIN_DEFAULT:
                if decision != current.decision: raise ScaleGatePersistenceError("automatic scale decision cannot be changed")
                return current
            if current.decision is None:
                if run["status"] != "AWAITING_SCALE_CONFIRMATION":
                    raise ScaleGatePersistenceError("Run is not awaiting scale confirmation")
                connection.execute("UPDATE landscape_v4_scale_gates SET decision=%s,decided_at=%s WHERE run_id=%s AND decision IS NULL", (decision.value, now, run_id))
                target = "READY" if approve else "CANCELLED"
                connection.execute("UPDATE landscape_v4_runs SET status=%s,updated_at=%s WHERE run_id=%s AND status='AWAITING_SCALE_CONFIRMATION'", (target, now, run_id))
            elif current.decision != decision:
                raise ScaleGatePersistenceError("scale decision is immutable")
            return self._load(connection, run_id)

    def get(self, run_id: str) -> ScaleGateRecord:
        with self._connect() as connection: return self._load(connection, run_id)

    @staticmethod
    def _load(connection, run_id: str) -> ScaleGateRecord:
        row = connection.execute("SELECT * FROM landscape_v4_scale_gates WHERE run_id=%s", (run_id,)).fetchone()
        if row is None: raise KeyError(run_id)
        estimate = ScaleEstimate(
            scope_revision_id=row["scope_revision_id"], scope_revision_hash=row["scope_revision_hash"],
            publication_start=row["publication_start"], publication_end=row["publication_end"],
            query_count=row["query_count"], estimated_total_results=row["estimated_total_results"],
            estimated_total_pages=row["estimated_total_pages"], estimated_shards=row["estimated_shards"], tier=row["tier"],
        )
        return ScaleGateRecord(run_id=run_id, estimate=estimate, decision=row["decision"], created_at=row["created_at"], decided_at=row["decided_at"])

    @contextmanager
    def _connect(self) -> Iterator[object]:
        if self._connect_factory is not None:
            with self._connect_factory() as connection: yield connection
            return
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(self._dsn, row_factory=dict_row, connect_timeout=10) as connection: yield connection


__all__ = ["PostgreSQLScaleGateRepository", "ScaleDecision", "ScaleGatePersistenceError", "ScaleGateRecord"]
