#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from idea.sqlite_export import SQLiteExportError, export_database, write_export  # noqa: E402


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export the current SQLite database read-only.")
    parser.add_argument(
        "--database",
        type=Path,
        default=PROJECT_ROOT / "data" / "aifpatent" / "aifpatent.db",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "workspace" / "migrations" / "aifpatent.sqlite-export.json",
    )
    args = parser.parse_args(argv)
    try:
        payload = export_database(args.database)
        output = write_export(payload, args.output)
    except SQLiteExportError as exc:
        print(f"SQLite export error: {exc}", file=sys.stderr)
        return 2
    print(
        f"exported {len(payload['tables'])} tables and "
        f"{sum(table['row_count'] for table in payload['tables'])} rows to {output}"
    )
    print(f"export_hash={payload['export_hash']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
