from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .schemas import LandscapeScope, RunStatus


TERMINAL_STATUSES = {
    RunStatus.COMPLETED.value,
    RunStatus.COMPLETED_WITH_LIMITATIONS.value,
    RunStatus.FAILED.value,
    RunStatus.CANCELLED.value,
}

ALLOWED_TRANSITIONS = {
    RunStatus.QUEUED.value: {RunStatus.RUNNING.value, RunStatus.FAILED.value, RunStatus.CANCELLED.value},
    RunStatus.RUNNING.value: TERMINAL_STATUSES,
}

SENSITIVE_KEY_PARTS = ("api_key", "apikey", "authorization", "password", "secret", "token")


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS landscape_runs (
    run_id TEXT PRIMARY KEY,
    parent_run_id TEXT REFERENCES landscape_runs(run_id) ON DELETE SET NULL,
    status TEXT NOT NULL CHECK(status IN ('QUEUED','RUNNING','COMPLETED','COMPLETED_WITH_LIMITATIONS','FAILED','CANCELLED')),
    mode TEXT NOT NULL CHECK(mode IN ('TECHNOLOGY','COMPETITOR','TECHNOLOGY_COMPETITOR')),
    publication_start TEXT NOT NULL,
    publication_end TEXT NOT NULL,
    model TEXT NOT NULL,
    workflow_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    scope_json TEXT NOT NULL,
    config_snapshot TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    limitation_json TEXT NOT NULL DEFAULT '[]',
    created_at INTEGER NOT NULL,
    started_at INTEGER,
    completed_at INTEGER,
    error_code TEXT,
    error_message TEXT
);
CREATE INDEX IF NOT EXISTS idx_landscape_runs_created ON landscape_runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_landscape_runs_status ON landscape_runs(status);

CREATE TABLE IF NOT EXISTS landscape_steps (
    step_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES landscape_runs(run_id) ON DELETE CASCADE,
    step_name TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1,
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
CREATE INDEX IF NOT EXISTS idx_landscape_steps_run ON landscape_steps(run_id, step_id);

CREATE TABLE IF NOT EXISTS landscape_stage_results (
    run_id TEXT NOT NULL REFERENCES landscape_runs(run_id) ON DELETE CASCADE,
    stage_name TEXT NOT NULL,
    result_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY(run_id, stage_name)
);

CREATE TABLE IF NOT EXISTS landscape_queries (
    query_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES landscape_runs(run_id) ON DELETE CASCADE,
    query_text TEXT NOT NULL,
    language TEXT NOT NULL,
    rationale TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS landscape_hits (
    hit_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES landscape_runs(run_id) ON DELETE CASCADE,
    query_id TEXT REFERENCES landscape_queries(query_id) ON DELETE SET NULL,
    provider TEXT NOT NULL,
    publication_number TEXT,
    application_number TEXT,
    publication_date TEXT,
    assignee TEXT,
    normalized_key TEXT,
    decision TEXT NOT NULL,
    exclusion_reason TEXT,
    raw_json TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_landscape_hits_run ON landscape_hits(run_id);

CREATE TABLE IF NOT EXISTS landscape_run_documents (
    run_id TEXT NOT NULL REFERENCES landscape_runs(run_id) ON DELETE CASCADE,
    document_id TEXT NOT NULL,
    publication_number TEXT NOT NULL,
    status TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    error_code TEXT,
    error_message TEXT,
    PRIMARY KEY(run_id, document_id),
    UNIQUE(run_id, publication_number)
);

CREATE TABLE IF NOT EXISTS landscape_evidence (
    evidence_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES landscape_runs(run_id) ON DELETE CASCADE,
    document_id TEXT NOT NULL,
    section_type TEXT NOT NULL,
    section_label TEXT NOT NULL,
    quote_text TEXT NOT NULL,
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS landscape_patent_analyses (
    run_id TEXT NOT NULL REFERENCES landscape_runs(run_id) ON DELETE CASCADE,
    document_id TEXT NOT NULL,
    publication_number TEXT NOT NULL,
    analysis_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY(run_id, document_id)
);

CREATE TABLE IF NOT EXISTS landscape_clusters (
    cluster_id TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES landscape_runs(run_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    summary TEXT NOT NULL,
    keywords_json TEXT NOT NULL,
    PRIMARY KEY(run_id, cluster_id)
);

CREATE TABLE IF NOT EXISTS landscape_cluster_members (
    run_id TEXT NOT NULL,
    cluster_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    publication_number TEXT NOT NULL,
    PRIMARY KEY(run_id, document_id),
    FOREIGN KEY(run_id, cluster_id) REFERENCES landscape_clusters(run_id, cluster_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS landscape_reports (
    run_id TEXT PRIMARY KEY REFERENCES landscape_runs(run_id) ON DELETE CASCADE,
    report_json_path TEXT NOT NULL,
    report_json_hash TEXT NOT NULL,
    report_md_path TEXT NOT NULL,
    report_md_hash TEXT NOT NULL,
    patents_csv_path TEXT NOT NULL,
    patents_csv_hash TEXT NOT NULL,
    manifest_path TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TRIGGER IF NOT EXISTS prevent_landscape_run_immutable_update
BEFORE UPDATE ON landscape_runs
WHEN OLD.parent_run_id IS NOT NEW.parent_run_id
  OR OLD.mode IS NOT NEW.mode
  OR OLD.publication_start IS NOT NEW.publication_start
  OR OLD.publication_end IS NOT NEW.publication_end
  OR OLD.model IS NOT NEW.model
  OR OLD.workflow_version IS NOT NEW.workflow_version
  OR OLD.prompt_version IS NOT NEW.prompt_version
  OR OLD.scope_json IS NOT NEW.scope_json
  OR OLD.config_snapshot IS NOT NEW.config_snapshot
  OR OLD.input_hash IS NOT NEW.input_hash
  OR OLD.created_at IS NOT NEW.created_at
BEGIN
    SELECT RAISE(ABORT, 'immutable landscape run fields cannot be updated');
END;
"""


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def assert_no_secrets(value: Any, path: str = "root") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).casefold().replace("-", "_")
            if any(part in normalized for part in SENSITIVE_KEY_PARTS):
                raise ValueError(f"sensitive field cannot be persisted: {path}.{key}")
            assert_no_secrets(child, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            assert_no_secrets(child, f"{path}[{index}]")


class LandscapeDatabase:
    """SQLite repository owned only by the landscape bounded context."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA_SQL)
            connection.execute("PRAGMA user_version = 1")

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

    def create_run(
        self,
        *,
        scope: LandscapeScope,
        model: str,
        workflow_version: str,
        prompt_version: str,
        config_snapshot: dict[str, Any] | None = None,
        parent_run_id: str | None = None,
    ) -> dict[str, Any]:
        model = model.strip()
        if not model:
            raise ValueError("model cannot be blank")
        config = config_snapshot or {}
        assert_no_secrets(config)
        scope_value = scope.model_dump(mode="json")
        assert_no_secrets(scope_value)
        scope_json = canonical_json(scope_value)
        run_id = str(uuid.uuid4())
        timestamp = now_ms()
        with self.connect() as connection:
            if parent_run_id is not None:
                parent = connection.execute(
                    "SELECT run_id FROM landscape_runs WHERE run_id = ?", (parent_run_id,)
                ).fetchone()
                if parent is None:
                    raise ValueError("parent landscape run does not exist")
            storage_mode = scope.mode.value
            if storage_mode == "TECHNOLOGY_COMPETITOR":
                schema = connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='landscape_runs'"
                ).fetchone()["sql"]
                if "'TECHNOLOGY_COMPETITOR'" not in schema:
                    # Databases created before this mode existed retain their immutable table.
                    # scope_json remains authoritative and exposes the derived combined mode.
                    storage_mode = "COMPETITOR"
            connection.execute(
                """
                INSERT INTO landscape_runs(
                    run_id,parent_run_id,status,mode,publication_start,publication_end,
                    model,workflow_version,prompt_version,scope_json,config_snapshot,
                    input_hash,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    run_id,
                    parent_run_id,
                    RunStatus.QUEUED.value,
                    storage_mode,
                    scope.publication_start.isoformat(),
                    scope.publication_end.isoformat(),
                    model,
                    workflow_version,
                    prompt_version,
                    scope_json,
                    canonical_json(config),
                    hashlib.sha256(scope_json.encode("utf-8")).hexdigest(),
                    timestamp,
                ),
            )
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM landscape_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        result = dict(row)
        for field in ("scope_json", "config_snapshot", "limitation_json"):
            result[field] = json.loads(result[field])
        result["mode"] = result["scope_json"]["mode"]
        return result

    def list_runs(self, limit: int = 100) -> list[dict[str, Any]]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT run_id,parent_run_id,status,mode,publication_start,publication_end,
                       model,input_hash,scope_json,limitation_json,created_at,started_at,
                       completed_at,error_code
                FROM landscape_runs ORDER BY created_at DESC,rowid DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        result = [dict(row) for row in rows]
        for item in result:
            item["limitation_json"] = json.loads(item["limitation_json"])
            scope = json.loads(item.pop("scope_json"))
            item["mode"] = scope["mode"]
        return result

    def set_run_status(
        self,
        run_id: str,
        status: RunStatus | str,
        *,
        limitations: list[dict[str, Any]] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        target = RunStatus(status).value
        if limitations is not None:
            assert_no_secrets(limitations)
        with self.connect() as connection:
            row = connection.execute(
                "SELECT status FROM landscape_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            current = row["status"]
            if target == current:
                return
            if target not in ALLOWED_TRANSITIONS.get(current, set()):
                raise ValueError(f"invalid run status transition: {current} -> {target}")
            timestamp = now_ms()
            connection.execute(
                """
                UPDATE landscape_runs
                SET status=?,limitation_json=COALESCE(?,limitation_json),
                    started_at=COALESCE(started_at,?),completed_at=?,
                    error_code=?,error_message=?
                WHERE run_id=?
                """,
                (
                    target,
                    canonical_json(limitations) if limitations is not None else None,
                    timestamp if target == RunStatus.RUNNING.value else None,
                    timestamp if target in TERMINAL_STATUSES else None,
                    error_code,
                    error_message,
                    run_id,
                ),
            )

    def mark_interrupted_runs_failed(self) -> int:
        timestamp = now_ms()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE landscape_runs
                SET status='FAILED',completed_at=?,error_code='RUNTIME_API_KEY_REQUIRED_AFTER_RESTART',
                    error_message='服务重启后临时 API Key 已清除，请创建新的重跑任务。'
                WHERE status IN ('QUEUED','RUNNING')
                """,
                (timestamp,),
            )
            return cursor.rowcount

    def put_stage_result(self, run_id: str, stage_name: str, value: Any) -> dict[str, Any]:
        assert_no_secrets(value)
        encoded = canonical_json(value)
        content_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT result_json,content_hash,created_at FROM landscape_stage_results WHERE run_id=? AND stage_name=?",
                (run_id, stage_name),
            ).fetchone()
            if existing is not None:
                if existing["content_hash"] != content_hash or existing["result_json"] != encoded:
                    raise ValueError(f"landscape stage result is immutable: {stage_name}")
            else:
                connection.execute(
                    "INSERT INTO landscape_stage_results VALUES(?,?,?,?,?)",
                    (run_id, stage_name, encoded, content_hash, now_ms()),
                )
        return self.get_stage_result(run_id, stage_name)

    def get_stage_result(self, run_id: str, stage_name: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT result_json,content_hash,created_at FROM landscape_stage_results WHERE run_id=? AND stage_name=?",
                (run_id, stage_name),
            ).fetchone()
        if row is None:
            raise KeyError((run_id, stage_name))
        actual = hashlib.sha256(row["result_json"].encode("utf-8")).hexdigest()
        if actual != row["content_hash"]:
            raise ValueError(f"landscape stage result hash mismatch: {stage_name}")
        return {"value": json.loads(row["result_json"]), "content_hash": row["content_hash"], "created_at": row["created_at"]}

    def put_document(
        self,
        run_id: str,
        *,
        document_id: str,
        publication_number: str,
        status: str,
        metadata: dict[str, Any],
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        assert_no_secrets(metadata)
        encoded = canonical_json(metadata)
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT publication_number,metadata_json FROM landscape_run_documents WHERE run_id=? AND document_id=?",
                (run_id, document_id),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO landscape_run_documents(
                        run_id,document_id,publication_number,status,metadata_json,error_code,error_message
                    ) VALUES(?,?,?,?,?,?,?)
                    """,
                    (run_id, document_id, publication_number, status, encoded, error_code, error_message),
                )
            elif existing["publication_number"] != publication_number or existing["metadata_json"] != encoded:
                raise ValueError(f"landscape document metadata is immutable: {document_id}")
            else:
                connection.execute(
                    """
                    UPDATE landscape_run_documents SET status=?,error_code=?,error_message=?
                    WHERE run_id=? AND document_id=?
                    """,
                    (status, error_code, error_message, run_id, document_id),
                )

    def put_queries(self, run_id: str, queries: list[dict[str, Any]]) -> None:
        assert_no_secrets(queries)
        with self.connect() as connection:
            for query in queries:
                values = (
                    query["query_id"], run_id, query["query_text"], query["language"],
                    query["rationale"], now_ms(),
                )
                existing = connection.execute(
                    "SELECT * FROM landscape_queries WHERE query_id=?", (query["query_id"],)
                ).fetchone()
                if existing is None:
                    connection.execute("INSERT INTO landscape_queries VALUES(?,?,?,?,?,?)", values)
                elif tuple(existing[key] for key in (
                    "query_id", "run_id", "query_text", "language", "rationale"
                )) != values[:-1]:
                    raise ValueError(f"landscape query is immutable: {query['query_id']}")

    def put_hit(
        self,
        run_id: str,
        *,
        hit_id: str,
        query_id: str,
        provider: str,
        publication_number: str | None,
        application_number: str | None,
        publication_date: str | None,
        assignee: str | None,
        normalized_key: str | None,
        decision: str,
        exclusion_reason: str | None,
        raw: dict[str, Any],
    ) -> None:
        assert_no_secrets(raw)
        encoded = canonical_json(raw)
        with self.connect() as connection:
            values = (
                hit_id, run_id, query_id, provider, publication_number, application_number,
                publication_date, assignee, normalized_key, decision, exclusion_reason,
                encoded, now_ms(),
            )
            existing = connection.execute(
                "SELECT * FROM landscape_hits WHERE hit_id=?", (hit_id,)
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO landscape_hits VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", values
                )
            elif tuple(existing[key] for key in (
                "hit_id", "run_id", "query_id", "provider", "publication_number",
                "application_number", "publication_date", "assignee", "normalized_key",
                "decision", "exclusion_reason", "raw_json",
            )) != values[:-1]:
                raise ValueError(f"landscape hit is immutable: {hit_id}")

    def put_evidence(self, run_id: str, document_id: str, items: list[dict[str, Any]]) -> None:
        assert_no_secrets(items)
        with self.connect() as connection:
            for item in items:
                values = (
                    item["evidence_id"], run_id, document_id, item["section_type"],
                    item["section_label"], item["text"], item["start_offset"],
                    item["end_offset"], item["content_hash"], now_ms(),
                )
                existing = connection.execute(
                    "SELECT * FROM landscape_evidence WHERE evidence_id=?", (item["evidence_id"],)
                ).fetchone()
                if existing is None:
                    connection.execute(
                        "INSERT INTO landscape_evidence VALUES(?,?,?,?,?,?,?,?,?,?)", values
                    )
                elif tuple(existing[key] for key in (
                    "evidence_id", "run_id", "document_id", "section_type", "section_label",
                    "quote_text", "start_offset", "end_offset", "content_hash",
                )) != values[:-1]:
                    raise ValueError(f"landscape evidence is immutable: {item['evidence_id']}")

    def put_analysis(
        self,
        run_id: str,
        document_id: str,
        publication_number: str,
        analysis: dict[str, Any],
    ) -> str:
        assert_no_secrets(analysis)
        encoded = canonical_json(analysis)
        content_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT analysis_json,content_hash FROM landscape_patent_analyses WHERE run_id=? AND document_id=?",
                (run_id, document_id),
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO landscape_patent_analyses VALUES(?,?,?,?,?,?)",
                    (run_id, document_id, publication_number, encoded, content_hash, now_ms()),
                )
            elif existing["analysis_json"] != encoded or existing["content_hash"] != content_hash:
                raise ValueError(f"landscape patent analysis is immutable: {document_id}")
        return content_hash

    def put_clusters(
        self,
        run_id: str,
        clusters: list[dict[str, Any]],
        document_ids: dict[str, str],
    ) -> None:
        assert_no_secrets(clusters)
        with self.connect() as connection:
            existing_rows = connection.execute(
                "SELECT * FROM landscape_clusters WHERE run_id=? ORDER BY cluster_id", (run_id,)
            ).fetchall()
            if existing_rows:
                existing = []
                for row in existing_rows:
                    members = connection.execute(
                        """
                        SELECT publication_number FROM landscape_cluster_members
                        WHERE run_id=? AND cluster_id=? ORDER BY publication_number
                        """,
                        (run_id, row["cluster_id"]),
                    ).fetchall()
                    existing.append(
                        {
                            "cluster_id": row["cluster_id"],
                            "name": row["name"],
                            "summary": row["summary"],
                            "keywords": json.loads(row["keywords_json"]),
                            "publication_numbers": [item["publication_number"] for item in members],
                        }
                    )
                expected = [
                    {**cluster, "publication_numbers": sorted(cluster["publication_numbers"])}
                    for cluster in sorted(clusters, key=lambda item: item["cluster_id"])
                ]
                if canonical_json(existing) != canonical_json(expected):
                    raise ValueError("landscape clusters are immutable")
                return
            for cluster in clusters:
                connection.execute(
                    "INSERT INTO landscape_clusters VALUES(?,?,?,?,?)",
                    (
                        cluster["cluster_id"], run_id, cluster["name"], cluster["summary"],
                        canonical_json(cluster.get("keywords", [])),
                    ),
                )
                for publication_number in cluster["publication_numbers"]:
                    connection.execute(
                        "INSERT INTO landscape_cluster_members VALUES(?,?,?,?)",
                        (run_id, cluster["cluster_id"], document_ids[publication_number], publication_number),
                    )

    def put_report_record(
        self,
        run_id: str,
        *,
        report_json_path: str,
        report_json_hash: str,
        report_md_path: str,
        report_md_hash: str,
        patents_csv_path: str,
        patents_csv_hash: str,
        manifest_path: str,
    ) -> None:
        with self.connect() as connection:
            values = (
                run_id, report_json_path, report_json_hash, report_md_path, report_md_hash,
                patents_csv_path, patents_csv_hash, manifest_path, now_ms(),
            )
            existing = connection.execute(
                "SELECT * FROM landscape_reports WHERE run_id=?", (run_id,)
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO landscape_reports VALUES(?,?,?,?,?,?,?,?,?)", values
                )
            elif tuple(existing[key] for key in (
                "run_id", "report_json_path", "report_json_hash", "report_md_path",
                "report_md_hash", "patents_csv_path", "patents_csv_hash", "manifest_path",
            )) != values[:-1]:
                raise ValueError("landscape report record is immutable")

    def delete_run(self, run_id: str) -> None:
        with self.connect() as connection:
            cursor = connection.execute("DELETE FROM landscape_runs WHERE run_id=?", (run_id,))
            if cursor.rowcount != 1:
                raise KeyError(run_id)

    def debug_snapshot(self, run_id: str) -> dict[str, Any]:
        """Return an operational view without raw provider/model payloads or credentials."""
        run = self.get_run(run_id)
        with self.connect() as connection:
            step_rows = connection.execute(
                """
                SELECT step_name,attempt,status,input_hash,output_hash,error_code,error_message,
                       started_at,completed_at
                FROM landscape_steps WHERE run_id=? ORDER BY step_id
                """,
                (run_id,),
            ).fetchall()
            query_rows = connection.execute(
                """
                SELECT query_id,query_text,language,rationale,created_at
                FROM landscape_queries WHERE run_id=? ORDER BY created_at,query_id
                """,
                (run_id,),
            ).fetchall()
            hit_rows = connection.execute(
                """
                SELECT provider,decision,COALESCE(exclusion_reason,'') exclusion_reason,
                       COUNT(*) count
                FROM landscape_hits WHERE run_id=?
                GROUP BY provider,decision,COALESCE(exclusion_reason,'')
                ORDER BY provider,decision,exclusion_reason
                """,
                (run_id,),
            ).fetchall()

        coverage: dict[str, Any] = {}
        candidate_ranking: list[dict[str, Any]] = []
        try:
            filtered = self.get_stage_result(run_id, "FILTER_AND_SELECT")["value"]
            coverage = filtered.get("result", {}).get("coverage", {})
            candidate_ranking = filtered.get("result", {}).get("ranking", [])[:200]
        except KeyError:
            pass
        aliases: list[dict[str, Any]] = []
        alias_resolution_error = None
        direction_expansion = None
        planned_queries: list[dict[str, Any]] = []
        try:
            plan = self.get_stage_result(run_id, "PLAN_SEARCH")["value"]
            aliases = list(plan.get("competitor_aliases", []))
            alias_resolution_error = plan.get("alias_resolution_error")
            direction_expansion = plan.get("technical_direction_expansion")
            planned_queries = plan.get("plan", {}).get("queries", [])
        except KeyError:
            pass
        searchable = " ".join(
            str(item.get("query_text", "")) for item in planned_queries
        ).casefold()
        for item in aliases:
            recognized = list(item.get("aliases", []))
            item["searched_aliases"] = [
                alias for alias in recognized if alias.casefold() in searchable
            ]
            item["unsearched_aliases"] = [
                alias for alias in recognized if alias.casefold() not in searchable
            ]

        provider_attempts: list[dict[str, Any]] = []
        try:
            search = self.get_stage_result(run_id, "SEARCH_PUBLICATIONS")["value"]
            provider_attempts = [
                {
                    "provider": item.get("provider"),
                    "request_id": item.get("request_id"),
                    "status": item.get("status"),
                    "duration_ms": item.get("duration_ms", 0),
                    "hit_count": len(item.get("hits", [])),
                    "error_code": item.get("error_code"),
                    "error_message": str(item.get("error_message") or "")[:500] or None,
                }
                for item in search.get("results", [])
            ]
        except KeyError:
            pass
        enrichment: dict[str, int] = {}
        try:
            filtered = self.get_stage_result(run_id, "FILTER_AND_SELECT")["value"]
            enrichment = filtered.get("enrichment", {})
        except KeyError:
            pass

        steps = []
        for row in step_rows:
            item = dict(row)
            started_at = item.get("started_at")
            completed_at = item.get("completed_at")
            item["duration_ms"] = (
                max(0, completed_at - started_at)
                if started_at is not None and completed_at is not None
                else None
            )
            steps.append(item)
        return {
            "run_id": run_id,
            "status": run["status"],
            "mode": run["mode"],
            "steps": steps,
            "queries": [dict(row) for row in query_rows],
            "competitor_aliases": aliases,
            "alias_resolution_error": alias_resolution_error,
            "technical_direction_expansion": direction_expansion,
            "provider_statuses": coverage.get("provider_statuses", {}),
            "provider_attempts": provider_attempts,
            "enrichment": enrichment,
            "coverage": {
                key: coverage.get(key, default)
                for key, default in (
                    ("raw_hit_count", 0),
                    ("eligible_hit_count", 0),
                    ("unique_candidate_count", 0),
                    ("selected_count", 0),
                    ("truncated_count", 0),
                    ("excluded_counts", {}),
                    ("company_patent_counts", []),
                )
            },
            "candidate_ranking": candidate_ranking,
            "hit_stats": [dict(row) for row in hit_rows],
        }

    def table_names(self) -> set[str]:
        with self.connect() as connection:
            rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        return {row["name"] for row in rows}
