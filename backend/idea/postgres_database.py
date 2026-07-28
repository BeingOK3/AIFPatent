from __future__ import annotations

"""Synchronous PostgreSQL adapters for the existing repository contracts.

The first cut-over deliberately keeps the domain repository methods stable.  The
adapter translates the small SQLite-specific surface used by those methods and
returns JSON columns in the string form expected by the legacy domain code.
New code must use PostgreSQL-native repositories and must not add SQL here.
"""

import hashlib
import json
import re
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from .database import RUN_STATUSES, canonical_json, now_ms


class PostgreSQLPersistenceError(RuntimeError):
    pass


_JSON_COLUMNS = {
    "analysis_json",
    "attachments_json",
    "config_snapshot",
    "details_json",
    "evidence_ids_json",
    "found_by_json",
    "inventors_json",
    "keywords_json",
    "limitation_json",
    "matrix_json",
    "metadata_json",
    "output_json",
    "query_ids_json",
    "raw_json",
    "request_json",
    "response_summary_json",
    "result_json",
    "scope_json",
    "settings_json",
}

# Legacy repositories sometimes omit the INSERT column list.  These positions
# are the JSONB columns in schema order (zero based).
_POSITIONAL_JSON_COLUMNS = {
    "audit_results": {3},
    "inventive_routes": {4, 6},
    "landscape_clusters": {4},
    "landscape_hits": {11},
    "landscape_patent_analyses": {3},
    "landscape_stage_results": {2},
    "novelty_results": {5, 7},
    "reports": set(),
    "stage_results": {2},
    "value_results": {1},
}


def _cast_json_placeholders(sql: str) -> str:
    """Add PostgreSQL JSONB casts to legacy SQLite-style parameter SQL."""

    insert = re.search(
        r"INSERT\s+INTO\s+([a-zA-Z_][a-zA-Z0-9_]*)"
        r"\s*(?:\((.*?)\))?\s*VALUES\s*\((.*?)\)",
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if insert:
        table = insert.group(1).lower()
        columns_text = insert.group(2)
        values = [item.strip() for item in insert.group(3).split(",")]
        if columns_text is not None:
            columns = [
                item.strip().strip('"').lower() for item in columns_text.split(",")
            ]
            json_positions = {
                index
                for index, column in enumerate(columns)
                if column in _JSON_COLUMNS
            }
        else:
            json_positions = _POSITIONAL_JSON_COLUMNS.get(table, set())
        changed = False
        for index in json_positions:
            if index >= len(values):
                continue
            if re.fullmatch(r"%s", values[index], flags=re.IGNORECASE):
                values[index] = "%s::jsonb"
                changed = True
        if changed:
            start, end = insert.span(3)
            sql = sql[:start] + ",".join(values) + sql[end:]

    for column in _JSON_COLUMNS:
        sql = re.sub(
            rf"(\b{column}\b\s*=\s*)%s(?!\s*::jsonb)",
            r"\1%s::jsonb",
            sql,
            flags=re.IGNORECASE,
        )
        sql = re.sub(
            rf"(\b{column}\b\s*=\s*COALESCE\(\s*)%s(?!\s*::jsonb)",
            r"\1%s::jsonb",
            sql,
            flags=re.IGNORECASE,
        )
    return sql


def _translate_sql(sql: str) -> str:
    value = sql
    value = value.replace("BEGIN IMMEDIATE", "BEGIN")
    value = value.replace(
        "title = ? COLLATE NOCASE", "LOWER(title) = LOWER(?)"
    )
    value = value.replace("COLLATE NOCASE", "")
    value = re.sub(r"\bchar\(", "chr(", value, flags=re.IGNORECASE)
    value = value.replace("INSERT OR IGNORE", "INSERT")
    value = value.replace("INSERT OR REPLACE", "INSERT")
    value = re.sub(
        r"\bdeep_reviewed\s*=\s*0\b", "deep_reviewed = FALSE", value, flags=re.IGNORECASE
    )
    value = re.sub(
        r"\bdeep_reviewed\s*=\s*1\b", "deep_reviewed = TRUE", value, flags=re.IGNORECASE
    )
    value = re.sub(r"\browid\b", "run_id", value, flags=re.IGNORECASE)
    value = re.sub(r"\?", "%s", value)
    if "sqlite_master" in value:
        return "SELECT 'TECHNOLOGY_COMPETITOR' AS sql"
    value = _cast_json_placeholders(value)
    if "INSERT INTO patent_families" in value and "ON CONFLICT" not in value:
        value = value.rstrip().rstrip(";") + " ON CONFLICT (family_id) DO NOTHING"
    if "INSERT INTO evidence" in value and "ON CONFLICT" not in value:
        value = value.rstrip().rstrip(";") + " ON CONFLICT (evidence_id) DO NOTHING"
    return value


def _json_compatible(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return value


class _Row(dict[str, Any]):
    """Dict row with sqlite-compatible positional access for legacy callers."""

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return tuple(self.values())[key]
        return super().__getitem__(key)


class _Cursor:
    def __init__(self, cursor: Any):
        self._cursor = cursor

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    @staticmethod
    def _row(row: Any) -> Any:
        if row is None:
            return None
        if isinstance(row, dict):
            return _Row({key: _json_compatible(value) for key, value in row.items()})
        return tuple(_json_compatible(value) for value in row)

    def fetchone(self) -> Any:
        return self._row(self._cursor.fetchone())

    def fetchall(self) -> list[Any]:
        return [self._row(row) for row in self._cursor.fetchall()]


class _Connection:
    def __init__(self, connection: Any):
        self._connection = connection

    def execute(self, sql: str, params: Any = ()) -> _Cursor:
        translated = _translate_sql(sql)
        if "INSERT INTO run_documents" in translated and "deep_reviewed" in translated:
            params = tuple(bool(value) if value in (0, 1) else value for value in params)
        cursor = self._connection.execute(translated, params)
        return _Cursor(cursor)

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()


class PostgreSQLDatabase:
    """IDEA repository using PostgreSQL while preserving the domain contract."""

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
    def connect(self) -> Iterator[_Connection]:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover - dependency is deployment-only
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

    def create_case(self, title: str) -> dict[str, Any]:
        clean_title = title.strip()
        if not clean_title:
            raise ValueError("case title cannot be empty")
        timestamp = now_ms()
        case_id = str(uuid.uuid4())
        with self.connect() as connection:
            duplicate = connection.execute(
                "SELECT case_id FROM idea_cases WHERE LOWER(title) = LOWER(?)",
                (clean_title,),
            ).fetchone()
            if duplicate is not None:
                raise ValueError("case title already exists")
            connection.execute(
                """
                INSERT INTO idea_cases(case_id,title,created_at,updated_at)
                VALUES(?,?,?,?)
                """,
                (case_id, clean_title, timestamp, timestamp),
            )
        return self.get_case(case_id)

    def get_case(self, case_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM idea_cases WHERE case_id = ?", (case_id,)
            ).fetchone()
        if row is None:
            raise KeyError(case_id)
        result = dict(row)
        result["runs"] = self.list_runs(case_id)
        return result

    def list_cases(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT c.*, COUNT(r.run_id) AS run_count,
                       MAX(r.created_at) AS latest_run_at
                FROM idea_cases c
                LEFT JOIN idea_runs r ON r.case_id = c.case_id
                GROUP BY c.case_id
                ORDER BY COALESCE(MAX(r.created_at), c.created_at) DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def create_run(
        self,
        *,
        case_id: str,
        input_text: str,
        evaluation_date: str,
        date_basis: str,
        analysis_scope: str,
        model: str,
        skill_version: str,
        workflow_version: str,
        config_snapshot: dict[str, Any],
        attachments: list[dict[str, Any]] | None = None,
        settings: dict[str, Any] | None = None,
        parent_run_id: str | None = None,
    ) -> dict[str, Any]:
        if not input_text.strip():
            raise ValueError("run input cannot be empty")
        run_id = str(uuid.uuid4())
        timestamp = now_ms()
        input_hash = hashlib.sha256(input_text.encode("utf-8")).hexdigest()
        with self.connect() as connection:
            case_exists = connection.execute(
                "SELECT 1 FROM idea_cases WHERE case_id = ?", (case_id,)
            ).fetchone()
            if case_exists is None:
                raise KeyError(case_id)
            if parent_run_id is not None:
                parent = connection.execute(
                    "SELECT case_id FROM idea_runs WHERE run_id = ?", (parent_run_id,)
                ).fetchone()
                if parent is None or parent["case_id"] != case_id:
                    raise ValueError("parent run must belong to the same case")
            connection.execute(
                """
                INSERT INTO idea_runs(
                    run_id,case_id,parent_run_id,status,evaluation_date,date_basis,
                    analysis_scope,model,skill_version,workflow_version,config_snapshot,
                    limitation_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?::jsonb,?::jsonb,?)
                """,
                (
                    run_id,
                    case_id,
                    parent_run_id,
                    "QUEUED",
                    evaluation_date,
                    date_basis,
                    analysis_scope,
                    model,
                    skill_version,
                    workflow_version,
                    canonical_json(config_snapshot),
                    "[]",
                    timestamp,
                ),
            )
            connection.execute(
                """
                INSERT INTO run_inputs(
                    run_id,input_text,input_hash,attachments_json,settings_json
                ) VALUES(?,?,?,?::jsonb,?::jsonb)
                """,
                (
                    run_id,
                    input_text,
                    input_hash,
                    canonical_json(attachments or []),
                    canonical_json(settings or {}),
                ),
            )
            connection.execute(
                "UPDATE idea_cases SET updated_at = ? WHERE case_id = ?",
                (timestamp, case_id),
            )
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT r.*, i.input_text, i.input_hash,
                       i.attachments_json, i.settings_json
                FROM idea_runs r
                JOIN run_inputs i ON i.run_id = r.run_id
                WHERE r.run_id = ?
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        result = dict(row)
        for field in (
            "config_snapshot",
            "limitation_json",
            "attachments_json",
            "settings_json",
        ):
            if isinstance(result[field], str):
                result[field] = json.loads(result[field])
        return result

    def put_stage_result(
        self, run_id: str, stage_name: str, value: Any
    ) -> dict[str, Any]:
        encoded = canonical_json(value)
        content_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        with self.connect() as connection:
            existing = connection.execute(
                """
                SELECT result_json,content_hash FROM stage_results
                WHERE run_id = ? AND stage_name = ?
                """,
                (run_id, stage_name),
            ).fetchone()
            if existing is not None:
                if (
                    existing["content_hash"] != content_hash
                    or existing["result_json"] != encoded
                ):
                    raise ValueError(f"stage result is immutable: {stage_name}")
            else:
                connection.execute(
                    """
                    INSERT INTO stage_results(
                        run_id,stage_name,result_json,content_hash,created_at
                    ) VALUES(?,?,?::jsonb,?,?)
                    """,
                    (run_id, stage_name, encoded, content_hash, now_ms()),
                )
        result = self.get_stage_result(run_id, stage_name)
        if result is None:  # pragma: no cover - transaction invariant
            raise PostgreSQLPersistenceError("stage result disappeared after insert")
        return result

    def get_stage_result(
        self, run_id: str, stage_name: str
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT result_json,content_hash,created_at FROM stage_results
                WHERE run_id = ? AND stage_name = ?
                """,
                (run_id, stage_name),
            ).fetchone()
        if row is None:
            return None
        encoded = row["result_json"]
        actual = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        if actual != row["content_hash"]:
            raise PostgreSQLPersistenceError(
                f"stage result hash mismatch: {stage_name}"
            )
        return {
            "value": json.loads(encoded),
            "content_hash": row["content_hash"],
            "created_at": row["created_at"],
        }

    def list_runs(self, case_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT r.run_id,r.case_id,r.parent_run_id,r.status,r.evaluation_date,
                       r.analysis_scope,r.model,r.limitation_json,r.created_at,
                       r.started_at,r.completed_at,r.error_code,i.input_hash,
                       substr(replace(replace(i.input_text, chr(13), ' '), chr(10), ' '), 1, 160)
                           AS input_preview
                FROM idea_runs r
                JOIN run_inputs i ON i.run_id = r.run_id
                WHERE r.case_id = ?
                ORDER BY r.created_at DESC, r.run_id DESC
                """,
                (case_id,),
            ).fetchall()
        result = [dict(row) for row in rows]
        for item in result:
            if isinstance(item["limitation_json"], str):
                item["limitation_json"] = json.loads(item["limitation_json"])
        return result

    def set_run_status(
        self,
        run_id: str,
        status: str,
        *,
        limitations: list[dict[str, Any]] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        if status not in RUN_STATUSES:
            raise ValueError(f"unknown run status: {status}")
        allowed = {
            "QUEUED": {"RUNNING", "FAILED", "CANCELLED"},
            "RUNNING": {
                "COMPLETED",
                "COMPLETED_WITH_LIMITATIONS",
                "FAILED",
                "CANCELLED",
            },
        }
        timestamp = now_ms()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT status FROM idea_runs WHERE run_id = ? FOR UPDATE", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            current = str(row["status"])
            if current == status:
                return
            if status not in allowed.get(current, set()):
                raise ValueError(f"invalid run status transition: {current} -> {status}")
            completed_at = (
                timestamp if status not in {"QUEUED", "RUNNING"} else None
            )
            cursor = connection.execute(
                """
                UPDATE idea_runs
                SET status = ?,
                    limitation_json = COALESCE(?::jsonb, limitation_json),
                    started_at = COALESCE(started_at, ?),
                    completed_at = ?,
                    error_code = ?,
                    error_message = ?
                WHERE run_id = ? AND status = ?
                """,
                (
                    status,
                    canonical_json(limitations) if limitations is not None else None,
                    timestamp if status == "RUNNING" else None,
                    completed_at,
                    error_code,
                    error_message,
                    run_id,
                    current,
                ),
            )
            if cursor.rowcount != 1:
                raise PostgreSQLPersistenceError("concurrent run status update detected")

    def delete_run(self, run_id: str, operator_label: str | None = None) -> None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT case_id FROM idea_runs WHERE run_id = ? FOR UPDATE", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            connection.execute("DELETE FROM idea_runs WHERE run_id = ?", (run_id,))
            connection.execute(
                """
                INSERT INTO deletion_events(
                    event_id,entity_type,entity_id,operator_label,deleted_at
                ) VALUES(?,?,?,?,?)
                """,
                (str(uuid.uuid4()), "run", run_id, operator_label, now_ms()),
            )
            connection.execute(
                "UPDATE idea_cases SET updated_at = ? WHERE case_id = ?",
                (now_ms(), row["case_id"]),
            )

    def delete_case(self, case_id: str, operator_label: str | None = None) -> None:
        with self.connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM idea_cases WHERE case_id = ? FOR UPDATE", (case_id,)
            ).fetchone()
            if exists is None:
                raise KeyError(case_id)
            connection.execute("DELETE FROM idea_cases WHERE case_id = ?", (case_id,))
            connection.execute(
                """
                INSERT INTO deletion_events(
                    event_id,entity_type,entity_id,operator_label,deleted_at
                ) VALUES(?,?,?,?,?)
                """,
                (str(uuid.uuid4()), "case", case_id, operator_label, now_ms()),
            )


__all__ = ["PostgreSQLDatabase", "PostgreSQLPersistenceError"]
