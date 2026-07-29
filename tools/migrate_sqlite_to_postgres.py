#!/usr/bin/env python3
from __future__ import annotations

"""One-way migration of legacy SQLite business data into PostgreSQL.

The application no longer uses SQLite after the cut-over.  This tool is kept as
an offline, repeatable import/verification utility and intentionally fails on
schema or identity surprises instead of silently overwriting data.
"""

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any


IDEA_TABLES = (
    "idea_cases",
    "idea_runs",
    "run_inputs",
    "run_steps",
    "stage_results",
    "tool_calls",
    "search_queries",
    "search_hits",
    "patent_families",
    "patent_documents",
    "run_documents",
    "idea_features",
    "evidence",
    "feature_mappings",
    "novelty_results",
    "inventive_routes",
    "value_results",
    "audit_results",
    "reports",
    "artifacts",
    "deletion_events",
)

LANDSCAPE_TABLES = (
    "landscape_runs",
    "landscape_steps",
    "landscape_stage_results",
    "landscape_queries",
    "landscape_hits",
    "landscape_run_documents",
    "landscape_evidence",
    "landscape_patent_analyses",
    "landscape_clusters",
    "landscape_cluster_members",
    "landscape_reports",
)

IDENTITY_COLUMNS = {"run_steps": {"step_id"}, "landscape_steps": {"step_id"}}
PRIMARY_KEYS = {
    "idea_cases": ("case_id",),
    "idea_runs": ("run_id",),
    "run_inputs": ("run_id",),
    "run_steps": ("run_id", "step_name", "attempt"),
    "stage_results": ("run_id", "stage_name"),
    "tool_calls": ("call_id",),
    "search_queries": ("query_id",),
    "search_hits": ("hit_id",),
    "patent_families": ("family_id",),
    "patent_documents": ("document_id",),
    "run_documents": ("run_id", "document_id"),
    "idea_features": ("feature_id",),
    "evidence": ("evidence_id",),
    "feature_mappings": ("mapping_id",),
    "novelty_results": ("run_id",),
    "inventive_routes": ("route_id",),
    "value_results": ("run_id",),
    "audit_results": ("audit_id",),
    "reports": ("run_id",),
    "artifacts": ("artifact_id",),
    "deletion_events": ("event_id",),
    "landscape_runs": ("run_id",),
    "landscape_steps": ("run_id", "step_name", "attempt"),
    "landscape_stage_results": ("run_id", "stage_name"),
    "landscape_queries": ("query_id",),
    "landscape_hits": ("hit_id",),
    "landscape_run_documents": ("run_id", "document_id"),
    "landscape_evidence": ("evidence_id",),
    "landscape_patent_analyses": ("run_id", "document_id"),
    "landscape_clusters": ("run_id", "cluster_id"),
    "landscape_cluster_members": ("run_id", "document_id"),
    "landscape_reports": ("run_id",),
}
DOCUMENT_ID_COLUMNS = {
    ("patent_documents", "document_id"),
    ("run_documents", "document_id"),
    ("evidence", "document_id"),
    ("feature_mappings", "document_id"),
    ("novelty_results", "destroying_document_id"),
    ("inventive_routes", "d1_document_id"),
}


class MigrationError(RuntimeError):
    pass


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sqlite_tables(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {str(row[0]) for row in rows}


def _sqlite_columns(connection: sqlite3.Connection, table: str) -> tuple[str, ...]:
    return tuple(
        str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    )


def _postgres_columns(connection: Any, table: str) -> dict[str, str]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = current_schema() AND table_name = %s
            ORDER BY ordinal_position
            """,
            (table,),
        )
        return {str(row[0]): str(row[1]) for row in cursor.fetchall()}


def _postgres_tables(connection: Any) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = current_schema()
            """
        )
        return {str(row[0]) for row in cursor.fetchall()}


def _adapt(value: Any, *, data_type: str) -> Any:
    if data_type == "boolean" and value is not None:
        return bool(value)
    if data_type != "jsonb":
        return value
    try:
        from psycopg.types.json import Jsonb
    except ImportError as exc:  # pragma: no cover
        raise MigrationError("psycopg is required for PostgreSQL migration") from exc
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise MigrationError("invalid JSON value in SQLite source") from exc
    return Jsonb(value)


def _document_id_map(source: sqlite3.Connection, target: Any) -> dict[str, str]:
    if "patent_documents" not in _sqlite_tables(source):
        return {}
    rows = source.execute(
        """
        SELECT document_id, publication_number, COALESCE(language, '') AS language
        FROM patent_documents
        """
    ).fetchall()
    mapping = {str(row["document_id"]): str(row["document_id"]) for row in rows}
    with target.cursor() as cursor:
        cursor.execute(
            """
            SELECT document_id, publication_number, COALESCE(language, '') AS language
            FROM patent_documents
            """
        )
        target_by_identity = {
            (str(row[1]), str(row[2])): str(row[0]) for row in cursor.fetchall()
        }
    for row in rows:
        identity = (str(row["publication_number"]), str(row["language"]))
        mapping[str(row["document_id"])] = target_by_identity.get(
            identity, str(row["document_id"])
        )
    return mapping


def _mapped_value(
    *,
    table: str,
    column: str,
    value: Any,
    document_ids: dict[str, str],
) -> Any:
    if value is None:
        return None
    if (table, column) in DOCUMENT_ID_COLUMNS:
        return document_ids.get(str(value), str(value))
    if table == "inventive_routes" and column == "d2_document_ids_json":
        identifiers = json.loads(value) if isinstance(value, str) else value
        return _json([document_ids.get(str(item), str(item)) for item in identifiers])
    return value


def _copy_table(
    *,
    source: sqlite3.Connection,
    target: Any,
    table: str,
    apply: bool,
    document_ids: dict[str, str],
) -> dict[str, int]:
    source_tables = _sqlite_tables(source)
    target_columns = _postgres_columns(target, table)
    if table not in source_tables:
        return {"source": 0, "inserted": 0, "skipped": 0}
    if not target_columns:
        raise MigrationError(f"PostgreSQL target table is missing: {table}")
    source_columns = _sqlite_columns(source, table)
    columns = [
        column
        for column in source_columns
        if column in target_columns and column not in IDENTITY_COLUMNS.get(table, set())
    ]
    if not columns:
        return {"source": 0, "inserted": 0, "skipped": 0}
    rows = source.execute(f'SELECT {",".join(columns)} FROM "{table}"').fetchall()
    if not apply:
        return {"source": len(rows), "inserted": 0, "skipped": 0}
    placeholders = ",".join(["%s"] * len(columns))
    query = (
        f'INSERT INTO "{table}" ({",".join(columns)}) '
        f"VALUES ({placeholders}) ON CONFLICT DO NOTHING"
    )
    inserted = 0
    with target.cursor() as cursor:
        for row in rows:
            values = tuple(
                _adapt(
                    _mapped_value(
                        table=table,
                        column=column,
                        value=row[column],
                        document_ids=document_ids,
                    ),
                    data_type=target_columns[column],
                )
                for column in columns
            )
            cursor.execute(query, values)
            inserted += max(0, cursor.rowcount)
    return {"source": len(rows), "inserted": inserted, "skipped": len(rows) - inserted}


def _copy_database(
    *,
    sqlite_path: Path,
    target: Any,
    tables: tuple[str, ...],
    apply: bool,
) -> dict[str, dict[str, int]]:
    if not sqlite_path.is_file():
        raise MigrationError(f"SQLite source does not exist: {sqlite_path}")
    source = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    try:
        missing = set(tables) & (_sqlite_tables(source) - set(tables))
        if missing:
            raise MigrationError(f"unexpected source table selection: {sorted(missing)}")
        results = {}
        document_ids = _document_id_map(source, target)
        for table in tables:
            results[table] = _copy_table(
                source=source,
                target=target,
                table=table,
                apply=apply,
                document_ids=document_ids,
            )
        return results
    finally:
        source.close()


def _verify_database(
    *,
    sqlite_path: Path,
    target: Any,
    tables: tuple[str, ...],
) -> dict[str, Any]:
    source = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    try:
        source_tables = _sqlite_tables(source)
        target_tables = _postgres_tables(target)
        document_ids = _document_id_map(source, target)
        result: dict[str, Any] = {"tables": {}, "ok": True}
        for table in tables:
            if table not in source_tables:
                continue
            if table not in target_tables:
                result["tables"][table] = {"error": "target_missing"}
                result["ok"] = False
                continue
            keys = PRIMARY_KEYS[table]
            source_rows = source.execute(
                f'SELECT {",".join(keys)} FROM "{table}"'
            ).fetchall()
            source_keys = {
                tuple(
                    _mapped_value(
                        table=table,
                        column=column,
                        value=row[column],
                        document_ids=document_ids,
                    )
                    for column in keys
                )
                for row in source_rows
            }
            with target.cursor() as cursor:
                cursor.execute(f'SELECT {",".join(keys)} FROM "{table}"')
                target_keys = {tuple(row) for row in cursor.fetchall()}
            missing = sorted(source_keys - target_keys, key=repr)
            item = {
                "source": len(source_keys),
                "target": len(target_keys),
                "extra_target": len(target_keys - source_keys),
                "missing_source_keys": len(missing),
            }
            if missing:
                item["error"] = "source_key_missing"
                item["missing_sample"] = [list(value) for value in missing[:10]]
                result["ok"] = False
            result["tables"][table] = item
        return result
    finally:
        source.close()


def _connect(dsn: str) -> Any:
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover
        raise MigrationError("psycopg is required for PostgreSQL migration") from exc
    return psycopg.connect(dsn, connect_timeout=10)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sqlite",
        type=Path,
        default=Path("data/aifpatent/aifpatent.db"),
        help="legacy IDEA SQLite database",
    )
    parser.add_argument(
        "--landscape-sqlite",
        type=Path,
        help="optional legacy Landscape SQLite database",
    )
    parser.add_argument(
        "--dsn",
        default=os.environ.get("AIFPATENT_POSTGRES_DSN", ""),
        help="PostgreSQL DSN (defaults to AIFPATENT_POSTGRES_DSN)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="inspect source rows without writing (default)",
    )
    mode.add_argument("--apply", action="store_true", help="write missing rows to PostgreSQL")
    mode.add_argument(
        "--verify",
        action="store_true",
        help="verify every legacy source key exists in PostgreSQL",
    )
    args = parser.parse_args(argv)
    if not args.dsn.strip():
        parser.error("--dsn or AIFPATENT_POSTGRES_DSN is required")

    target = _connect(args.dsn)
    try:
        apply = bool(args.apply)
        if args.verify:
            idea = _verify_database(sqlite_path=args.sqlite, target=target, tables=IDEA_TABLES)
            landscapes = (
                _verify_database(
                    sqlite_path=args.landscape_sqlite,
                    target=target,
                    tables=LANDSCAPE_TABLES,
                )
                if args.landscape_sqlite
                else {"ok": True, "tables": {}}
            )
            payload = {"idea": idea, "landscape": landscapes, "ok": idea["ok"] and landscapes["ok"]}
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
            return 0 if payload["ok"] else 2

        results = {
            "idea": _copy_database(
                sqlite_path=args.sqlite, target=target, tables=IDEA_TABLES, apply=apply
            )
        }
        if args.landscape_sqlite:
            results["landscape"] = _copy_database(
                sqlite_path=args.landscape_sqlite,
                target=target,
                tables=LANDSCAPE_TABLES,
                apply=apply,
            )
        if apply:
            target.commit()
        print(
            json.dumps(
                {"mode": "apply" if apply else "dry-run", "results": results},
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
        )
        return 0
    except Exception as exc:
        target.rollback()
        print(f"migration failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        target.close()


if __name__ == "__main__":
    raise SystemExit(main())
