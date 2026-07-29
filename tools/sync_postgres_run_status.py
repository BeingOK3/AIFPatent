#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from idea.database import Database  # noqa: E402
from idea.postgres_corpus import PostgreSQLCorpusPrerequisiteRepository  # noqa: E402


async def synchronize(
    *, sqlite_path: Path, dsn: str, include_nonterminal: bool = False
) -> tuple[int, int]:
    database = Database(sqlite_path)
    database.initialize()
    statuses = (
        "('QUEUED','RUNNING','COMPLETED','COMPLETED_WITH_LIMITATIONS','FAILED','CANCELLED')"
        if include_nonterminal
        else "('COMPLETED','COMPLETED_WITH_LIMITATIONS','FAILED','CANCELLED')"
    )
    with database.connect() as connection:
        rows = connection.execute(
            f"SELECT run_id FROM idea_runs WHERE status IN {statuses} ORDER BY created_at"
        ).fetchall()
    repository = PostgreSQLCorpusPrerequisiteRepository(database, dsn)
    synchronized = 0
    for row in rows:
        if await repository.sync_run_status(str(row["run_id"])):
            synchronized += 1
    return len(rows), synchronized


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Synchronize mutable SQLite Run lifecycle fields into the PostgreSQL bridge."
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="include QUEUED/RUNNING rows; default synchronizes terminal Runs only",
    )
    parser.add_argument(
        "--sqlite",
        type=Path,
        default=PROJECT_ROOT / "data" / "aifpatent" / "aifpatent.db",
        help="legacy SQLite source database",
    )
    parser.add_argument(
        "--dsn",
        default=os.environ.get("AIFPATENT_POSTGRES_DSN", ""),
        help="PostgreSQL DSN (defaults to AIFPATENT_POSTGRES_DSN)",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.dsn.strip():
        raise SystemExit("--dsn or AIFPATENT_POSTGRES_DSN is required")
    source_count, synchronized = asyncio.run(
        synchronize(
            sqlite_path=args.sqlite,
            dsn=args.dsn,
            include_nonterminal=args.all,
        )
    )
    print(f"source_runs={source_count} synchronized={synchronized}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
