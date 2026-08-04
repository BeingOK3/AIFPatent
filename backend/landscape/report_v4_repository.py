from __future__ import annotations

import json
import time
from contextlib import contextmanager
from typing import Iterator

from pydantic import ValidationError

from .report_v4 import LandscapeReportV4, REPORT_SCHEMA_VERSION


class ReportV4PersistenceError(RuntimeError):
    pass


class PostgreSQLReportV4Repository:
    def __init__(self, dsn: str, *, connect=None):
        if not isinstance(dsn, str) or not dsn.strip():
            raise ValueError("PostgreSQL DSN must not be blank")
        self._dsn = dsn.strip()
        self._connect_factory = connect

    def put(
        self,
        report: LandscapeReportV4,
        markdown: str,
    ) -> tuple[LandscapeReportV4, str]:
        report = LandscapeReportV4.model_validate(report.model_dump(mode="json"))
        if not markdown.strip():
            raise ValueError("report markdown must not be blank")
        try:
            from psycopg.types.json import Jsonb
        except ImportError as exc:  # pragma: no cover
            raise ReportV4PersistenceError("psycopg is required for report persistence") from exc
        now = int(time.time() * 1000)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO landscape_v4_reports(
                    run_id,schema_version,report_hash,report_json,report_markdown,created_at
                ) VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (run_id) DO NOTHING
                """,
                (
                    report.run_id,
                    report.schema_version,
                    report.report_hash,
                    Jsonb(report.model_dump(mode="json")),
                    markdown,
                    now,
                ),
            )
            stored = self._load(connection, report.run_id)
            if stored != (report, markdown):
                raise ReportV4PersistenceError("Report 4.0 artifact is immutable")
            return stored

    def get(self, run_id: str) -> tuple[LandscapeReportV4, str]:
        with self._connect() as connection:
            return self._load(connection, run_id)

    @staticmethod
    def _load(connection, run_id: str) -> tuple[LandscapeReportV4, str]:
        row = connection.execute(
            "SELECT * FROM landscape_v4_reports WHERE run_id=%s",
            (run_id,),
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        if row["schema_version"] != REPORT_SCHEMA_VERSION:
            raise ReportV4PersistenceError("unknown Landscape report schema version")
        payload = row["report_json"]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise ReportV4PersistenceError("stored report JSON is invalid") from exc
        try:
            report = LandscapeReportV4.model_validate(payload)
        except (ValidationError, TypeError, ValueError) as exc:
            raise ReportV4PersistenceError("stored Report 4.0 failed validation") from exc
        if report.report_hash != row["report_hash"] or report.run_id != run_id:
            raise ReportV4PersistenceError("stored Report 4.0 identity mismatch")
        return report, row["report_markdown"]

    @contextmanager
    def _connect(self) -> Iterator[object]:
        if self._connect_factory is not None:
            with self._connect_factory() as connection:
                yield connection
            return
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(
            self._dsn, row_factory=dict_row, connect_timeout=10
        ) as connection:
            yield connection


__all__ = ["PostgreSQLReportV4Repository", "ReportV4PersistenceError"]
