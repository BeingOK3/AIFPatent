from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Iterator

from pydantic import ValidationError

from .scope_repository import PostgreSQLScopeDraftRepository, ScopePersistenceError
from .taxonomy_repository import PostgreSQLTaxonomyRepository, TaxonomyPersistenceError
from .v4_run import LandscapeRun, LandscapeRunStatus, make_landscape_run_id


class LandscapeRunPersistenceError(RuntimeError):
    pass


class PostgreSQLLandscapeRunRepository:
    """Fail-closed v4 Run repository bound to immutable Scope and Taxonomy."""

    def __init__(
        self,
        dsn: str,
        scope_repository: PostgreSQLScopeDraftRepository,
        taxonomy_repository: PostgreSQLTaxonomyRepository,
        *,
        connect=None,
    ):
        if not isinstance(dsn, str) or not dsn.strip():
            raise ValueError("PostgreSQL DSN must not be blank")
        self._dsn = dsn.strip()
        self.scope_repository = scope_repository
        self.taxonomy_repository = taxonomy_repository
        self._connect_factory = connect

    def create(
        self,
        *,
        scope_revision_id: str,
        taxonomy_version: str,
        run_id: str | None = None,
    ) -> LandscapeRun:
        try:
            scope = self.scope_repository.get_confirmed(scope_revision_id)
            taxonomy = self.taxonomy_repository.get(taxonomy_version)
        except (KeyError, ScopePersistenceError, TaxonomyPersistenceError) as exc:
            raise LandscapeRunPersistenceError(
                "run inputs must reference trusted confirmed scope and taxonomy revisions"
            ) from exc
        timestamp = int(time.time() * 1000)
        run = LandscapeRun(
            run_id=run_id or make_landscape_run_id(),
            scope_revision_id=scope.scope_revision_id,
            scope_revision_hash=scope.scope_revision_hash,
            taxonomy_version=taxonomy.taxonomy_version,
            taxonomy_hash=taxonomy.taxonomy_hash,
            status=LandscapeRunStatus.PLANNING,
            mode=scope.mode,
            publication_start=scope.publication_start,
            publication_end=scope.publication_end,
            workflow_version="landscape-v4/1.0.0",
            created_at=timestamp,
            updated_at=timestamp,
        )
        with self._connect() as connection:
            inserted = connection.execute(
                """
                INSERT INTO landscape_v4_runs(
                    run_id,scope_revision_id,scope_revision_hash,taxonomy_version,
                    taxonomy_hash,status,mode,publication_start,publication_end,
                    workflow_version,created_at,updated_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (run_id) DO NOTHING
                RETURNING run_id
                """,
                (
                    run.run_id, run.scope_revision_id, run.scope_revision_hash,
                    run.taxonomy_version, run.taxonomy_hash, run.status.value,
                    run.mode.value, run.publication_start, run.publication_end,
                    run.workflow_version, run.created_at, run.updated_at,
                ),
            ).fetchone()
            if inserted is None:
                existing = self._load(connection, run.run_id)
                if existing != run:
                    raise LandscapeRunPersistenceError(
                        "run ID already exists with different immutable inputs"
                    )
                return existing
        return self.get(run.run_id)

    def get(self, run_id: str) -> LandscapeRun:
        with self._connect() as connection:
            run = self._load(connection, run_id)
        self._validate_bindings(run)
        return run

    def list(self, limit: int = 100) -> tuple[LandscapeRun, ...]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT run_id,scope_revision_id,scope_revision_hash,taxonomy_version,
                       taxonomy_hash,status,mode,publication_start,publication_end,
                       workflow_version,created_at,updated_at,started_at,completed_at,
                       error_code,error_message
                FROM landscape_v4_runs
                ORDER BY created_at DESC,run_id DESC
                LIMIT %s
                """,
                (limit,),
            ).fetchall()
        runs = tuple(self._decode(row) for row in rows)
        for run in runs:
            self._validate_bindings(run)
        return runs

    def transition(
        self,
        run_id: str,
        target: LandscapeRunStatus,
        *,
        expected: tuple[LandscapeRunStatus, ...],
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> LandscapeRun:
        target = LandscapeRunStatus(target)
        if not expected:
            raise ValueError("expected run statuses must not be empty")
        expected_values = tuple(LandscapeRunStatus(value) for value in expected)
        if target == LandscapeRunStatus.FAILED:
            if not error_code or not error_code.strip():
                raise ValueError("failed transition requires error code")
        elif error_code or error_message:
            raise ValueError("only failed transition may contain an error")
        timestamp = int(time.time() * 1000)
        with self._connect() as connection:
            current = self._load_for_update(connection, run_id)
            if current.status == target:
                return current
            if current.status not in expected_values:
                raise LandscapeRunPersistenceError(
                    f"cannot transition {current.status.value} to {target.value}"
                )
            if target not in _ALLOWED_TRANSITIONS[current.status]:
                raise LandscapeRunPersistenceError(
                    f"transition {current.status.value} to {target.value} is not allowed"
                )
            terminal = target in {
                LandscapeRunStatus.COMPLETED,
                LandscapeRunStatus.COMPLETED_WITH_LIMITATIONS,
                LandscapeRunStatus.FAILED,
                LandscapeRunStatus.CANCELLED,
            }
            connection.execute(
                """
                UPDATE landscape_v4_runs
                SET status=%s,updated_at=%s,
                    started_at=CASE WHEN %s='RUNNING' THEN COALESCE(started_at,%s) ELSE started_at END,
                    completed_at=CASE WHEN %s THEN %s ELSE completed_at END,
                    error_code=%s,error_message=%s
                WHERE run_id=%s AND status=%s
                """,
                (
                    target.value,
                    timestamp,
                    target.value,
                    timestamp,
                    terminal,
                    timestamp,
                    error_code.strip() if error_code else None,
                    error_message.strip()[:2000] if error_message else None,
                    run_id,
                    current.status.value,
                ),
            )
            return self._load(connection, run_id)

    def cancel(self, run_id: str) -> LandscapeRun:
        current = self.get(run_id)
        if current.status in {
            LandscapeRunStatus.COMPLETED,
            LandscapeRunStatus.COMPLETED_WITH_LIMITATIONS,
            LandscapeRunStatus.FAILED,
            LandscapeRunStatus.CANCELLED,
        }:
            return current
        return self.transition(
            run_id,
            LandscapeRunStatus.CANCELLED,
            expected=(current.status,),
        )

    def _validate_bindings(self, run: LandscapeRun) -> None:
        try:
            scope = self.scope_repository.get_confirmed(run.scope_revision_id)
            taxonomy = self.taxonomy_repository.get(run.taxonomy_version)
        except (KeyError, ScopePersistenceError, TaxonomyPersistenceError) as exc:
            raise LandscapeRunPersistenceError("run binding cannot be replayed") from exc
        if scope.scope_revision_hash != run.scope_revision_hash:
            raise LandscapeRunPersistenceError("run scope hash does not match confirmed scope")
        if taxonomy.taxonomy_hash != run.taxonomy_hash:
            raise LandscapeRunPersistenceError("run taxonomy hash does not match taxonomy")
        if (
            scope.mode != run.mode
            or scope.publication_start != run.publication_start
            or scope.publication_end != run.publication_end
        ):
            raise LandscapeRunPersistenceError("run scope projection is inconsistent")

    @classmethod
    def _load(cls, connection, run_id: str) -> LandscapeRun:
        row = connection.execute(
            """
            SELECT run_id,scope_revision_id,scope_revision_hash,taxonomy_version,
                   taxonomy_hash,status,mode,publication_start,publication_end,
                   workflow_version,created_at,updated_at,started_at,completed_at,
                   error_code,error_message
            FROM landscape_v4_runs WHERE run_id=%s
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return cls._decode(row)

    @classmethod
    def _load_for_update(cls, connection, run_id: str) -> LandscapeRun:
        row = connection.execute(
            "SELECT * FROM landscape_v4_runs WHERE run_id=%s FOR UPDATE",
            (run_id,),
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return cls._decode(row)

    @staticmethod
    def _decode(row) -> LandscapeRun:
        try:
            return LandscapeRun.model_validate(dict(row))
        except (ValidationError, TypeError, ValueError) as exc:
            raise LandscapeRunPersistenceError("stored v4 run failed validation") from exc

    @contextmanager
    def _connect(self) -> Iterator[object]:
        if self._connect_factory is not None:
            with self._connect_factory() as connection:
                yield connection
            return
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover
            raise LandscapeRunPersistenceError(
                "psycopg is required for v4 Run persistence"
            ) from exc
        with psycopg.connect(
            self._dsn, row_factory=dict_row, connect_timeout=10
        ) as connection:
            yield connection


__all__ = ["LandscapeRunPersistenceError", "PostgreSQLLandscapeRunRepository"]


_ALLOWED_TRANSITIONS = {
    LandscapeRunStatus.PLANNING: {
        LandscapeRunStatus.ESTIMATING,
        LandscapeRunStatus.CANCELLED,
    },
    LandscapeRunStatus.ESTIMATING: {
        LandscapeRunStatus.AWAITING_SCALE_CONFIRMATION,
        LandscapeRunStatus.READY,
        LandscapeRunStatus.FAILED,
        LandscapeRunStatus.CANCELLED,
    },
    LandscapeRunStatus.AWAITING_SCALE_CONFIRMATION: {
        LandscapeRunStatus.READY,
        LandscapeRunStatus.CANCELLED,
        LandscapeRunStatus.FAILED,
    },
    LandscapeRunStatus.READY: {
        LandscapeRunStatus.RUNNING,
        LandscapeRunStatus.CANCELLED,
        LandscapeRunStatus.FAILED,
    },
    LandscapeRunStatus.RUNNING: {
        LandscapeRunStatus.WAITING_FOR_CREDENTIALS,
        LandscapeRunStatus.COMPLETED,
        LandscapeRunStatus.COMPLETED_WITH_LIMITATIONS,
        LandscapeRunStatus.FAILED,
        LandscapeRunStatus.CANCELLED,
    },
    LandscapeRunStatus.WAITING_FOR_CREDENTIALS: {
        LandscapeRunStatus.RUNNING,
        LandscapeRunStatus.CANCELLED,
        LandscapeRunStatus.FAILED,
    },
    LandscapeRunStatus.COMPLETED: set(),
    LandscapeRunStatus.COMPLETED_WITH_LIMITATIONS: set(),
    LandscapeRunStatus.FAILED: set(),
    LandscapeRunStatus.CANCELLED: set(),
}
