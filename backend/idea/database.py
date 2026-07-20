from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


RUN_STATUSES = {
    "QUEUED",
    "RUNNING",
    "COMPLETED",
    "COMPLETED_WITH_LIMITATIONS",
    "FAILED",
    "CANCELLED",
}


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS idea_cases (
    case_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    archived_at INTEGER
);

CREATE TABLE IF NOT EXISTS idea_runs (
    run_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES idea_cases(case_id) ON DELETE CASCADE,
    parent_run_id TEXT REFERENCES idea_runs(run_id) ON DELETE SET NULL,
    status TEXT NOT NULL CHECK (status IN ('QUEUED','RUNNING','COMPLETED','COMPLETED_WITH_LIMITATIONS','FAILED','CANCELLED')),
    evaluation_date TEXT NOT NULL,
    date_basis TEXT NOT NULL,
    analysis_scope TEXT NOT NULL,
    model TEXT NOT NULL,
    skill_version TEXT NOT NULL,
    workflow_version TEXT NOT NULL,
    config_snapshot TEXT NOT NULL,
    limitation_json TEXT NOT NULL DEFAULT '[]',
    created_at INTEGER NOT NULL,
    started_at INTEGER,
    completed_at INTEGER,
    error_code TEXT,
    error_message TEXT
);
CREATE INDEX IF NOT EXISTS idx_idea_runs_case_created ON idea_runs(case_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_idea_runs_status ON idea_runs(status);

CREATE TABLE IF NOT EXISTS run_inputs (
    run_id TEXT PRIMARY KEY REFERENCES idea_runs(run_id) ON DELETE CASCADE,
    input_text TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    attachments_json TEXT NOT NULL DEFAULT '[]',
    settings_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS run_steps (
    step_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES idea_runs(run_id) ON DELETE CASCADE,
    step_name TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    status TEXT NOT NULL,
    input_hash TEXT,
    output_hash TEXT,
    output_json TEXT,
    error_code TEXT,
    error_message TEXT,
    started_at INTEGER,
    completed_at INTEGER,
    UNIQUE(run_id, step_name, attempt)
);
CREATE INDEX IF NOT EXISTS idx_run_steps_run ON run_steps(run_id, step_id);

CREATE TABLE IF NOT EXISTS stage_results (
    run_id TEXT NOT NULL REFERENCES idea_runs(run_id) ON DELETE CASCADE,
    stage_name TEXT NOT NULL,
    result_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY(run_id, stage_name)
);

CREATE TABLE IF NOT EXISTS tool_calls (
    call_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES idea_runs(run_id) ON DELETE CASCADE,
    step_name TEXT NOT NULL,
    provider TEXT NOT NULL,
    operation TEXT NOT NULL,
    request_json TEXT NOT NULL,
    response_summary_json TEXT,
    result_count INTEGER,
    duration_ms INTEGER,
    status TEXT NOT NULL,
    error_code TEXT,
    error_message TEXT,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS search_queries (
    query_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES idea_runs(run_id) ON DELETE CASCADE,
    round_number INTEGER NOT NULL,
    query_type TEXT NOT NULL,
    language TEXT NOT NULL,
    query_text TEXT NOT NULL,
    rationale TEXT,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS search_hits (
    hit_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES idea_runs(run_id) ON DELETE CASCADE,
    query_id TEXT REFERENCES search_queries(query_id) ON DELETE SET NULL,
    provider TEXT NOT NULL,
    provider_rank INTEGER,
    title TEXT,
    url TEXT,
    publication_number TEXT,
    application_number TEXT,
    family_id TEXT,
    snippet TEXT,
    raw_json TEXT NOT NULL,
    normalized_key TEXT,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_search_hits_run ON search_hits(run_id);
CREATE INDEX IF NOT EXISTS idx_search_hits_publication ON search_hits(publication_number);

CREATE TABLE IF NOT EXISTS patent_families (
    family_id TEXT PRIMARY KEY,
    canonical_publication_number TEXT,
    source TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS patent_documents (
    document_id TEXT PRIMARY KEY,
    publication_number TEXT NOT NULL,
    application_number TEXT,
    family_id TEXT REFERENCES patent_families(family_id) ON DELETE SET NULL,
    title TEXT,
    assignee TEXT,
    inventors_json TEXT NOT NULL DEFAULT '[]',
    priority_date TEXT,
    filing_date TEXT,
    publication_date TEXT,
    grant_date TEXT,
    language TEXT,
    url TEXT,
    abstract_text TEXT,
    claims_text TEXT,
    description_text TEXT,
    content_hash TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(publication_number, language)
);

CREATE TABLE IF NOT EXISTS run_documents (
    run_id TEXT NOT NULL REFERENCES idea_runs(run_id) ON DELETE CASCADE,
    document_id TEXT NOT NULL REFERENCES patent_documents(document_id) ON DELETE RESTRICT,
    relevance TEXT,
    relevance_score REAL,
    screening_status TEXT NOT NULL DEFAULT 'CANDIDATE',
    deep_reviewed INTEGER NOT NULL DEFAULT 0,
    found_by_json TEXT NOT NULL DEFAULT '[]',
    query_ids_json TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY(run_id, document_id)
);

CREATE TABLE IF NOT EXISTS idea_features (
    feature_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES idea_runs(run_id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    feature_text TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_start INTEGER,
    source_end INTEGER,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(run_id, ordinal)
);

CREATE TABLE IF NOT EXISTS evidence (
    evidence_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES idea_runs(run_id) ON DELETE CASCADE,
    document_id TEXT NOT NULL REFERENCES patent_documents(document_id) ON DELETE RESTRICT,
    section_type TEXT NOT NULL,
    section_label TEXT NOT NULL,
    quote_text TEXT NOT NULL,
    start_offset INTEGER,
    end_offset INTEGER,
    content_hash TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS feature_mappings (
    mapping_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES idea_runs(run_id) ON DELETE CASCADE,
    document_id TEXT NOT NULL REFERENCES patent_documents(document_id) ON DELETE RESTRICT,
    feature_id TEXT NOT NULL REFERENCES idea_features(feature_id) ON DELETE CASCADE,
    coverage_status TEXT NOT NULL,
    confidence REAL NOT NULL,
    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    rationale TEXT NOT NULL,
    UNIQUE(run_id, document_id, feature_id)
);

CREATE TABLE IF NOT EXISTS novelty_results (
    run_id TEXT PRIMARY KEY REFERENCES idea_runs(run_id) ON DELETE CASCADE,
    conclusion TEXT NOT NULL,
    confidence REAL NOT NULL,
    destroying_document_id TEXT REFERENCES patent_documents(document_id) ON DELETE SET NULL,
    matrix_json TEXT NOT NULL,
    rationale TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS inventive_routes (
    route_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES idea_runs(run_id) ON DELETE CASCADE,
    d1_document_id TEXT NOT NULL REFERENCES patent_documents(document_id) ON DELETE RESTRICT,
    d2_document_ids_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS value_results (
    run_id TEXT PRIMARY KEY REFERENCES idea_runs(run_id) ON DELETE CASCADE,
    result_json TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_results (
    audit_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES idea_runs(run_id) ON DELETE CASCADE,
    severity TEXT NOT NULL,
    code TEXT NOT NULL,
    message TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS reports (
    run_id TEXT PRIMARY KEY REFERENCES idea_runs(run_id) ON DELETE CASCADE,
    report_json_path TEXT NOT NULL,
    report_json_hash TEXT NOT NULL,
    report_md_path TEXT NOT NULL,
    report_md_hash TEXT NOT NULL,
    manifest_path TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES idea_runs(run_id) ON DELETE CASCADE,
    artifact_type TEXT NOT NULL,
    path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS cache_entries (
    cache_key TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    path TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
    content_hash TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    sequence INTEGER NOT NULL UNIQUE,
    lease_count INTEGER NOT NULL DEFAULT 0 CHECK(lease_count >= 0)
);
CREATE INDEX IF NOT EXISTS idx_cache_fifo ON cache_entries(sequence);

CREATE TABLE IF NOT EXISTS deletion_events (
    event_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    operator_label TEXT,
    deleted_at INTEGER NOT NULL
);

CREATE TRIGGER IF NOT EXISTS prevent_run_immutable_update
BEFORE UPDATE ON idea_runs
WHEN OLD.case_id IS NOT NEW.case_id
  OR OLD.parent_run_id IS NOT NEW.parent_run_id
  OR OLD.evaluation_date IS NOT NEW.evaluation_date
  OR OLD.date_basis IS NOT NEW.date_basis
  OR OLD.analysis_scope IS NOT NEW.analysis_scope
  OR OLD.model IS NOT NEW.model
  OR OLD.skill_version IS NOT NEW.skill_version
  OR OLD.workflow_version IS NOT NEW.workflow_version
  OR OLD.config_snapshot IS NOT NEW.config_snapshot
  OR OLD.created_at IS NOT NEW.created_at
BEGIN
    SELECT RAISE(ABORT, 'immutable run fields cannot be updated');
END;

CREATE TRIGGER IF NOT EXISTS prevent_run_input_update
BEFORE UPDATE ON run_inputs
BEGIN
    SELECT RAISE(ABORT, 'run input is immutable');
END;
"""


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA_SQL)
            connection.execute("PRAGMA user_version = 2")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def create_case(self, title: str) -> dict[str, Any]:
        clean_title = title.strip()
        if not clean_title:
            raise ValueError("case title cannot be empty")
        timestamp = now_ms()
        case_id = str(uuid.uuid4())
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            duplicate = connection.execute(
                "SELECT case_id FROM idea_cases WHERE title = ? COLLATE NOCASE",
                (clean_title,),
            ).fetchone()
            if duplicate is not None:
                raise ValueError("case title already exists")
            connection.execute(
                "INSERT INTO idea_cases(case_id,title,created_at,updated_at) VALUES(?,?,?,?)",
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
        sql = """
        SELECT c.*, COUNT(r.run_id) AS run_count, MAX(r.created_at) AS latest_run_at
        FROM idea_cases c
        LEFT JOIN idea_runs r ON r.case_id = c.case_id
        GROUP BY c.case_id
        ORDER BY COALESCE(MAX(r.created_at), c.created_at) DESC
        """
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(sql).fetchall()]

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
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                INSERT INTO run_inputs(run_id,input_text,input_hash,attachments_json,settings_json)
                VALUES(?,?,?,?,?)
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
                SELECT r.*, i.input_text, i.input_hash, i.attachments_json, i.settings_json
                FROM idea_runs r JOIN run_inputs i ON i.run_id = r.run_id
                WHERE r.run_id = ?
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        result = dict(row)
        for field in ("config_snapshot", "limitation_json", "attachments_json", "settings_json"):
            result[field] = json.loads(result[field])
        return result

    def put_stage_result(self, run_id: str, stage_name: str, value: Any) -> dict[str, Any]:
        encoded = canonical_json(value)
        content_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT result_json,content_hash FROM stage_results WHERE run_id = ? AND stage_name = ?",
                (run_id, stage_name),
            ).fetchone()
            if existing:
                if existing["content_hash"] != content_hash or existing["result_json"] != encoded:
                    raise ValueError(f"stage result is immutable: {stage_name}")
            else:
                connection.execute(
                    "INSERT INTO stage_results VALUES(?,?,?,?,?)",
                    (run_id, stage_name, encoded, content_hash, now_ms()),
                )
        return self.get_stage_result(run_id, stage_name)

    def get_stage_result(self, run_id: str, stage_name: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT result_json,content_hash,created_at FROM stage_results WHERE run_id = ? AND stage_name = ?",
                (run_id, stage_name),
            ).fetchone()
        if row is None:
            return None
        actual = hashlib.sha256(row["result_json"].encode("utf-8")).hexdigest()
        if actual != row["content_hash"]:
            raise ValueError(f"stage result hash mismatch: {stage_name}")
        return {
            "value": json.loads(row["result_json"]),
            "content_hash": row["content_hash"],
            "created_at": row["created_at"],
        }

    def list_runs(self, case_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT r.run_id,r.case_id,r.parent_run_id,r.status,r.evaluation_date,
                       r.analysis_scope,r.model,r.limitation_json,r.created_at,r.started_at,
                       r.completed_at,r.error_code,i.input_hash,
                       substr(replace(replace(i.input_text, char(13), ' '), char(10), ' '), 1, 160)
                           AS input_preview
                FROM idea_runs r JOIN run_inputs i ON i.run_id = r.run_id
                WHERE r.case_id = ? ORDER BY r.created_at DESC, r.rowid DESC
                """,
                (case_id,),
            ).fetchall()
        result = [dict(row) for row in rows]
        for item in result:
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
        timestamp = now_ms()
        started_at = timestamp if status == "RUNNING" else None
        completed_at = timestamp if status in RUN_STATUSES - {"QUEUED", "RUNNING"} else None
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE idea_runs
                SET status = ?,
                    limitation_json = COALESCE(?, limitation_json),
                    started_at = COALESCE(started_at, ?),
                    completed_at = ?, error_code = ?, error_message = ?
                WHERE run_id = ?
                """,
                (
                    status,
                    canonical_json(limitations) if limitations is not None else None,
                    started_at,
                    completed_at,
                    error_code,
                    error_message,
                    run_id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(run_id)

    def delete_run(self, run_id: str, operator_label: str | None = None) -> None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT case_id FROM idea_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            connection.execute("DELETE FROM idea_runs WHERE run_id = ?", (run_id,))
            connection.execute(
                "INSERT INTO deletion_events VALUES(?,?,?,?,?)",
                (str(uuid.uuid4()), "run", run_id, operator_label, now_ms()),
            )
            connection.execute(
                "UPDATE idea_cases SET updated_at = ? WHERE case_id = ?",
                (now_ms(), row["case_id"]),
            )

    def delete_case(self, case_id: str, operator_label: str | None = None) -> None:
        with self.connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM idea_cases WHERE case_id = ?", (case_id,)
            ).fetchone()
            if exists is None:
                raise KeyError(case_id)
            connection.execute("DELETE FROM idea_cases WHERE case_id = ?", (case_id,))
            connection.execute(
                "INSERT INTO deletion_events VALUES(?,?,?,?,?)",
                (str(uuid.uuid4()), "case", case_id, operator_label, now_ms()),
            )

    def table_names(self) -> set[str]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        return {row["name"] for row in rows}
