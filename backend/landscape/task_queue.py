from __future__ import annotations

import hashlib
import time
import uuid
from contextlib import contextmanager
from enum import StrEnum
from typing import Iterator

from pydantic import Field

from .scope import ScopeModel


class TaskState(StrEnum):
    QUEUED = "QUEUED"
    LEASED = "LEASED"
    SUCCEEDED = "SUCCEEDED"
    UNRESOLVED = "UNRESOLVED"


class TaskRecord(ScopeModel):
    task_id: str = Field(pattern=r"^TSK-[0-9a-f]{16}$")
    run_id: str = Field(min_length=1)
    task_key: str = Field(min_length=1)
    task_type: str = Field(min_length=1)
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: TaskState
    attempts: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    lease_owner: str | None = None
    lease_expires_at: int | None = None
    last_error: str | None = None
    created_at: int = Field(ge=0)
    updated_at: int = Field(ge=0)


class TaskQueueError(RuntimeError):
    pass


class PostgreSQLTaskQueue:
    def __init__(self, dsn: str, *, connect=None, lease_ms: int = 60_000):
        if not isinstance(dsn, str) or not dsn.strip(): raise ValueError("PostgreSQL DSN must not be blank")
        if lease_ms < 1000: raise ValueError("lease_ms must be at least 1000")
        self._dsn = dsn.strip(); self._connect_factory = connect; self.lease_ms = lease_ms

    def enqueue(self, run_id: str, task_key: str, task_type: str, payload_hash: str, *, max_attempts: int = 3) -> TaskRecord:
        if not task_key.strip() or not task_type.strip(): raise ValueError("task key and type must not be blank")
        now = int(time.time() * 1000)
        task_id = f"TSK-{hashlib.sha256(f'{run_id}|{task_key}'.encode()).hexdigest()[:16]}"
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO landscape_v4_tasks(task_id,run_id,task_key,task_type,payload_hash,state,attempts,max_attempts,created_at,updated_at)
                VALUES (%s,%s,%s,%s,%s,'QUEUED',0,%s,%s,%s)
                ON CONFLICT (run_id,task_key) DO NOTHING
                """,
                (task_id,run_id,task_key,task_type,payload_hash,max_attempts,now,now),
            )
            stored = self._load(connection, run_id, task_key)
            if stored.payload_hash != payload_hash or stored.task_type != task_type or stored.max_attempts != max_attempts:
                raise TaskQueueError("task key already exists with different immutable payload")
            return stored

    def claim(self, owner: str, *, limit: int = 1) -> tuple[TaskRecord, ...]:
        if not owner.strip() or not 1 <= limit <= 100: raise ValueError("invalid claim owner or limit")
        now = int(time.time() * 1000); expiry = now + self.lease_ms
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT task_id FROM landscape_v4_tasks
                WHERE (state='QUEUED' OR (state='LEASED' AND lease_expires_at <= %s))
                ORDER BY created_at,task_id
                FOR UPDATE SKIP LOCKED LIMIT %s
                """, (now, limit),
            ).fetchall()
            claimed = []
            for row in rows:
                connection.execute(
                    """UPDATE landscape_v4_tasks SET state='LEASED',attempts=attempts+1,lease_owner=%s,lease_expires_at=%s,updated_at=%s WHERE task_id=%s""",
                    (owner, expiry, now, row["task_id"]),
                )
                claimed.append(self._load_by_id(connection, row["task_id"]))
            return tuple(claimed)

    def succeed(self, task_id: str, owner: str) -> TaskRecord:
        now = int(time.time() * 1000)
        with self._connect() as connection:
            row = self._load_by_id(connection, task_id)
            if row.state != TaskState.LEASED or row.lease_owner != owner: raise TaskQueueError("task lease is not owned by caller")
            connection.execute("UPDATE landscape_v4_tasks SET state='SUCCEEDED',lease_owner=NULL,lease_expires_at=NULL,updated_at=%s WHERE task_id=%s AND state='LEASED' AND lease_owner=%s", (now,task_id,owner))
            return self._load_by_id(connection, task_id)

    def fail(self, task_id: str, owner: str, error: str) -> TaskRecord:
        now = int(time.time() * 1000)
        with self._connect() as connection:
            row = self._load_by_id(connection, task_id)
            if row.state != TaskState.LEASED or row.lease_owner != owner: raise TaskQueueError("task lease is not owned by caller")
            terminal = row.attempts >= row.max_attempts
            state = TaskState.UNRESOLVED if terminal else TaskState.QUEUED
            connection.execute("UPDATE landscape_v4_tasks SET state=%s,lease_owner=NULL,lease_expires_at=NULL,last_error=%s,updated_at=%s WHERE task_id=%s AND state='LEASED' AND lease_owner=%s", (state.value,error[:2000],now,task_id,owner))
            return self._load_by_id(connection, task_id)

    def get(self, task_id: str) -> TaskRecord:
        with self._connect() as connection: return self._load_by_id(connection, task_id)

    @staticmethod
    def _decode(row) -> TaskRecord: return TaskRecord(**dict(row))
    @staticmethod
    def _load(connection, run_id, task_key):
        row=connection.execute("SELECT * FROM landscape_v4_tasks WHERE run_id=%s AND task_key=%s", (run_id,task_key)).fetchone()
        if row is None: raise KeyError((run_id,task_key))
        return PostgreSQLTaskQueue._decode(row)
    @staticmethod
    def _load_by_id(connection, task_id):
        row=connection.execute("SELECT * FROM landscape_v4_tasks WHERE task_id=%s", (task_id,)).fetchone()
        if row is None: raise KeyError(task_id)
        return PostgreSQLTaskQueue._decode(row)

    @contextmanager
    def _connect(self) -> Iterator[object]:
        if self._connect_factory is not None:
            with self._connect_factory() as connection: yield connection
            return
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(self._dsn, row_factory=dict_row, connect_timeout=10) as connection: yield connection


__all__ = ["PostgreSQLTaskQueue", "TaskQueueError", "TaskRecord", "TaskState"]
