from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any


EXPORT_VERSION = "sqlite-export/1"


class SQLiteExportError(RuntimeError):
    pass


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"__base64__": base64.b64encode(value).decode("ascii")}
    return value


def _read_only_connection(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise SQLiteExportError(f"SQLite database does not exist: {path}")
    try:
        connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        return connection
    except sqlite3.Error as exc:
        raise SQLiteExportError(f"cannot open SQLite database read-only: {path}") from exc


def export_database(path: str | Path) -> dict[str, Any]:
    """Create a deterministic, read-only export suitable for migration rehearsal."""

    database_path = Path(path).resolve()
    connection = _read_only_connection(database_path)
    try:
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise SQLiteExportError(f"SQLite integrity check failed: {integrity}")
        foreign_keys = [tuple(row) for row in connection.execute("PRAGMA foreign_key_check")]
        if foreign_keys:
            raise SQLiteExportError("SQLite foreign key check failed")

        tables: list[dict[str, Any]] = []
        table_rows = connection.execute(
            """
            SELECT name, sql FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        ).fetchall()
        for table_row in table_rows:
            name = table_row["name"]
            columns = [
                {
                    "name": column[1],
                    "type": column[2],
                    "not_null": bool(column[3]),
                    "default": column[4],
                    "primary_key_position": column[5],
                }
                for column in connection.execute(f'PRAGMA table_info("{name}")')
            ]
            rows = [
                {column: _value(row[column]) for column in row.keys()}
                for row in connection.execute(f'SELECT * FROM "{name}"')
            ]
            rows.sort(key=_canonical)
            tables.append(
                {
                    "name": name,
                    "create_sql": table_row["sql"],
                    "columns": columns,
                    "row_count": len(rows),
                    "rows": rows,
                }
            )
    except sqlite3.Error as exc:
        raise SQLiteExportError("SQLite export query failed") from exc
    finally:
        connection.close()

    payload: dict[str, Any] = {
        "export_version": EXPORT_VERSION,
        "source": {
            "path_name": database_path.name,
            "user_version": user_version,
        },
        "tables": tables,
    }
    encoded = _canonical(payload).encode("utf-8")
    payload["export_hash"] = hashlib.sha256(encoded).hexdigest()
    return payload


def write_export(payload: dict[str, Any], output: str | Path) -> Path:
    target = Path(output).resolve()
    if target.exists():
        raise SQLiteExportError(f"refusing to overwrite existing export: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = (_canonical(payload) + "\n").encode("utf-8")
    temporary_name: str | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(prefix=".sqlite-export-", dir=target.parent)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary_name, target, follow_symlinks=False)
        os.chmod(target, 0o600)
    except FileExistsError as exc:
        raise SQLiteExportError(f"refusing to overwrite existing export: {target}") from exc
    finally:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)
    return target


__all__ = ["EXPORT_VERSION", "SQLiteExportError", "export_database", "write_export"]
