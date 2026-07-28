from __future__ import annotations

from contextlib import contextmanager

from idea.postgres_database import PostgreSQLPersistenceError, _Connection

from .database import LandscapeDatabase


class LandscapePostgreSQLDatabase(LandscapeDatabase):
    """Landscape repository bound to the same PostgreSQL database as IDEA."""

    def __init__(self, dsn: str):
        if not dsn.strip():
            raise ValueError("PostgreSQL DSN must not be blank")
        self.dsn = dsn.strip()
        self.path = "<postgresql>"

    def initialize(self) -> None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT version FROM aifpatent_schema_migrations
                WHERE version = '060_unified_runtime_schema'
                """
            ).fetchone()
        if row is None:
            raise PostgreSQLPersistenceError(
                "PostgreSQL schema is not current; apply migration 060_unified_runtime_schema"
            )

    @contextmanager
    def connect(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover
            raise PostgreSQLPersistenceError("psycopg is required for PostgreSQL persistence") from exc
        raw = psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=10)
        connection = _Connection(raw)
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def table_names(self) -> set[str]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT table_name AS name
                FROM information_schema.tables
                WHERE table_schema = current_schema()
                """
            ).fetchall()
        return {row["name"] for row in rows}


__all__ = ["LandscapePostgreSQLDatabase"]
