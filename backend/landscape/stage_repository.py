from __future__ import annotations

import hashlib
import time
from contextlib import contextmanager
from enum import StrEnum
from typing import Iterator

from pydantic import Field, model_validator

from .scope import ScopeModel


class V4StageName(StrEnum):
    ESTIMATE_SCALE = "ESTIMATE_SCALE"
    RETRIEVE_PAGES = "RETRIEVE_PAGES"
    FREEZE_PUBLICATIONS = "FREEZE_PUBLICATIONS"
    RESOLVE_FAMILIES = "RESOLVE_FAMILIES"
    FETCH_ABSTRACTS = "FETCH_ABSTRACTS"
    EXTRACT_DIRECTIONS = "EXTRACT_DIRECTIONS"
    MATCH_TAXONOMY = "MATCH_TAXONOMY"
    DISCOVER_OTHERS = "DISCOVER_OTHERS"
    COMPUTE_METRICS = "COMPUTE_METRICS"
    BUILD_TRENDS = "BUILD_TRENDS"
    SELECT_REPRESENTATIVES = "SELECT_REPRESENTATIVES"
    BUILD_REPORT = "BUILD_REPORT"
    VERIFY_RUN = "VERIFY_RUN"


STAGE_ORDER = tuple(V4StageName)


class V4StageStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    SUCCEEDED = "SUCCEEDED"
    SUCCEEDED_WITH_LIMITATIONS = "SUCCEEDED_WITH_LIMITATIONS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class V4StageRecord(ScopeModel):
    run_id: str
    stage_name: V4StageName
    stage_order: int = Field(ge=1)
    status: V4StageStatus
    attempt: int = Field(ge=0)
    completed_count: int = Field(ge=0)
    total_count: int | None = Field(default=None, ge=0)
    error_code: str | None = None
    error_message: str | None = None
    started_at: int | None = Field(default=None, ge=0)
    completed_at: int | None = Field(default=None, ge=0)
    updated_at: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_progress(self) -> "V4StageRecord":
        if self.total_count is not None and self.completed_count > self.total_count:
            raise ValueError("stage completed count exceeds total")
        if self.status == V4StageStatus.FAILED and not self.error_code:
            raise ValueError("failed stage requires error code")
        return self


class V4RunLimitation(ScopeModel):
    run_id: str
    limitation_id: str = Field(pattern=r"^LIM-[0-9a-f]{16}$")
    stage_name: V4StageName
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,99}$")
    message: str = Field(min_length=1, max_length=2000)
    affected_count: int | None = Field(default=None, ge=0)
    created_at: int = Field(ge=0)


class StagePersistenceError(RuntimeError):
    pass


class PostgreSQLStageRepository:
    def __init__(self, dsn: str, *, connect=None):
        if not isinstance(dsn, str) or not dsn.strip():
            raise ValueError("PostgreSQL DSN must not be blank")
        self._dsn = dsn.strip()
        self._connect_factory = connect

    def ensure(self, run_id: str) -> tuple[V4StageRecord, ...]:
        now = int(time.time() * 1000)
        with self._connect() as connection:
            if connection.execute(
                "SELECT run_id FROM landscape_v4_runs WHERE run_id=%s",
                (run_id,),
            ).fetchone() is None:
                raise KeyError(run_id)
            with connection.cursor() as cursor:
                cursor.executemany(
                    """
                    INSERT INTO landscape_v4_run_stages(
                        run_id,stage_name,stage_order,status,attempt,
                        completed_count,total_count,updated_at
                    ) VALUES (%s,%s,%s,'PENDING',0,0,NULL,%s)
                    ON CONFLICT (run_id,stage_name) DO NOTHING
                    """,
                    [
                        (run_id, stage.value, index, now)
                        for index, stage in enumerate(STAGE_ORDER, start=1)
                    ],
                )
            stages = self._list(connection, run_id)
            if tuple(stage.stage_name for stage in stages) != STAGE_ORDER:
                raise StagePersistenceError("stored stage plan differs from workflow version")
            return stages

    def list(self, run_id: str) -> tuple[V4StageRecord, ...]:
        with self._connect() as connection:
            return self._list(connection, run_id)

    def start(
        self,
        run_id: str,
        stage_name: V4StageName,
        *,
        total_count: int | None = None,
    ) -> V4StageRecord:
        stage_name = V4StageName(stage_name)
        if total_count is not None and total_count < 0:
            raise ValueError("stage total count cannot be negative")
        now = int(time.time() * 1000)
        with self._connect() as connection:
            stages = self._list_for_update(connection, run_id)
            current = _stage(stages, stage_name)
            if current.status in {
                V4StageStatus.SUCCEEDED,
                V4StageStatus.SUCCEEDED_WITH_LIMITATIONS,
            }:
                return current
            predecessors = stages[: current.stage_order - 1]
            if any(
                item.status
                not in {
                    V4StageStatus.SUCCEEDED,
                    V4StageStatus.SUCCEEDED_WITH_LIMITATIONS,
                }
                for item in predecessors
            ):
                raise StagePersistenceError("stage predecessors are incomplete")
            if current.status not in {
                V4StageStatus.PENDING,
                V4StageStatus.WAITING,
                V4StageStatus.FAILED,
            }:
                raise StagePersistenceError("stage cannot be started from current state")
            connection.execute(
                """
                UPDATE landscape_v4_run_stages
                SET status='RUNNING',attempt=attempt+1,total_count=%s,
                    error_code=NULL,error_message=NULL,started_at=COALESCE(started_at,%s),
                    completed_at=NULL,updated_at=%s
                WHERE run_id=%s AND stage_name=%s
                """,
                (total_count, now, now, run_id, stage_name.value),
            )
            return self._load(connection, run_id, stage_name)

    def progress(
        self,
        run_id: str,
        stage_name: V4StageName,
        *,
        completed_count: int,
        total_count: int | None = None,
    ) -> V4StageRecord:
        now = int(time.time() * 1000)
        with self._connect() as connection:
            current = self._load_for_update(connection, run_id, stage_name)
            if current.status != V4StageStatus.RUNNING:
                raise StagePersistenceError("only a running stage can report progress")
            effective_total = current.total_count if total_count is None else total_count
            if completed_count < current.completed_count:
                raise StagePersistenceError("stage progress cannot move backwards")
            if effective_total is not None and completed_count > effective_total:
                raise StagePersistenceError("stage progress exceeds total")
            connection.execute(
                """
                UPDATE landscape_v4_run_stages
                SET completed_count=%s,total_count=%s,updated_at=%s
                WHERE run_id=%s AND stage_name=%s AND status='RUNNING'
                """,
                (completed_count, effective_total, now, run_id, V4StageName(stage_name).value),
            )
            return self._load(connection, run_id, stage_name)

    def succeed(
        self,
        run_id: str,
        stage_name: V4StageName,
        *,
        with_limitations: bool = False,
    ) -> V4StageRecord:
        now = int(time.time() * 1000)
        target = (
            V4StageStatus.SUCCEEDED_WITH_LIMITATIONS
            if with_limitations
            else V4StageStatus.SUCCEEDED
        )
        with self._connect() as connection:
            current = self._load_for_update(connection, run_id, stage_name)
            if current.status == target:
                return current
            if current.status != V4StageStatus.RUNNING:
                raise StagePersistenceError("only a running stage can succeed")
            completed = current.total_count if current.total_count is not None else current.completed_count
            connection.execute(
                """
                UPDATE landscape_v4_run_stages
                SET status=%s,completed_count=%s,completed_at=%s,updated_at=%s
                WHERE run_id=%s AND stage_name=%s AND status='RUNNING'
                """,
                (target.value, completed, now, now, run_id, V4StageName(stage_name).value),
            )
            return self._load(connection, run_id, stage_name)

    def fail(
        self,
        run_id: str,
        stage_name: V4StageName,
        *,
        error_code: str,
        error_message: str,
    ) -> V4StageRecord:
        if not error_code.strip():
            raise ValueError("stage failure requires error code")
        now = int(time.time() * 1000)
        with self._connect() as connection:
            current = self._load_for_update(connection, run_id, stage_name)
            if current.status != V4StageStatus.RUNNING:
                raise StagePersistenceError("only a running stage can fail")
            connection.execute(
                """
                UPDATE landscape_v4_run_stages
                SET status='FAILED',error_code=%s,error_message=%s,
                    completed_at=%s,updated_at=%s
                WHERE run_id=%s AND stage_name=%s AND status='RUNNING'
                """,
                (
                    error_code.strip()[:200],
                    error_message.strip()[:2000],
                    now,
                    now,
                    run_id,
                    V4StageName(stage_name).value,
                ),
            )
            return self._load(connection, run_id, stage_name)

    def add_limitation(
        self,
        run_id: str,
        stage_name: V4StageName,
        *,
        code: str,
        message: str,
        affected_count: int | None = None,
    ) -> V4RunLimitation:
        semantic = f"{run_id}|{V4StageName(stage_name).value}|{code}|{message}|{affected_count}"
        limitation_id = f"LIM-{hashlib.sha256(semantic.encode()).hexdigest()[:16]}"
        now = int(time.time() * 1000)
        value = V4RunLimitation(
            run_id=run_id,
            limitation_id=limitation_id,
            stage_name=stage_name,
            code=code,
            message=message,
            affected_count=affected_count,
            created_at=now,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO landscape_v4_run_limitations(
                    run_id,limitation_id,stage_name,code,message,affected_count,created_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (run_id,limitation_id) DO NOTHING
                """,
                (
                    value.run_id,
                    value.limitation_id,
                    value.stage_name.value,
                    value.code,
                    value.message,
                    value.affected_count,
                    value.created_at,
                ),
            )
            stored = self._load_limitation(connection, run_id, limitation_id)
            if stored.model_copy(update={"created_at": value.created_at}) != value:
                raise StagePersistenceError("limitation ID collision")
            return stored

    def limitations(self, run_id: str) -> tuple[V4RunLimitation, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM landscape_v4_run_limitations
                WHERE run_id=%s ORDER BY created_at,limitation_id
                """,
                (run_id,),
            ).fetchall()
            return tuple(V4RunLimitation.model_validate(dict(row)) for row in rows)

    @staticmethod
    def _list(connection, run_id: str) -> tuple[V4StageRecord, ...]:
        rows = connection.execute(
            """
            SELECT * FROM landscape_v4_run_stages
            WHERE run_id=%s ORDER BY stage_order
            """,
            (run_id,),
        ).fetchall()
        return tuple(V4StageRecord.model_validate(dict(row)) for row in rows)

    @staticmethod
    def _list_for_update(connection, run_id: str) -> tuple[V4StageRecord, ...]:
        rows = connection.execute(
            """
            SELECT * FROM landscape_v4_run_stages
            WHERE run_id=%s ORDER BY stage_order FOR UPDATE
            """,
            (run_id,),
        ).fetchall()
        if not rows:
            raise KeyError(run_id)
        return tuple(V4StageRecord.model_validate(dict(row)) for row in rows)

    @staticmethod
    def _load(connection, run_id: str, stage_name: V4StageName) -> V4StageRecord:
        row = connection.execute(
            """
            SELECT * FROM landscape_v4_run_stages
            WHERE run_id=%s AND stage_name=%s
            """,
            (run_id, V4StageName(stage_name).value),
        ).fetchone()
        if row is None:
            raise KeyError((run_id, stage_name))
        return V4StageRecord.model_validate(dict(row))

    @staticmethod
    def _load_for_update(connection, run_id: str, stage_name: V4StageName) -> V4StageRecord:
        row = connection.execute(
            """
            SELECT * FROM landscape_v4_run_stages
            WHERE run_id=%s AND stage_name=%s FOR UPDATE
            """,
            (run_id, V4StageName(stage_name).value),
        ).fetchone()
        if row is None:
            raise KeyError((run_id, stage_name))
        return V4StageRecord.model_validate(dict(row))

    @staticmethod
    def _load_limitation(connection, run_id: str, limitation_id: str) -> V4RunLimitation:
        row = connection.execute(
            """
            SELECT * FROM landscape_v4_run_limitations
            WHERE run_id=%s AND limitation_id=%s
            """,
            (run_id, limitation_id),
        ).fetchone()
        if row is None:
            raise KeyError((run_id, limitation_id))
        return V4RunLimitation.model_validate(dict(row))

    @contextmanager
    def _connect(self) -> Iterator[object]:
        if self._connect_factory is not None:
            with self._connect_factory() as connection:
                yield connection
            return
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(self._dsn, row_factory=dict_row, connect_timeout=10) as connection:
            yield connection


def _stage(stages: tuple[V4StageRecord, ...], name: V4StageName) -> V4StageRecord:
    for stage in stages:
        if stage.stage_name == name:
            return stage
    raise KeyError(name)


__all__ = [
    "PostgreSQLStageRepository",
    "STAGE_ORDER",
    "StagePersistenceError",
    "V4RunLimitation",
    "V4StageName",
    "V4StageRecord",
    "V4StageStatus",
]
