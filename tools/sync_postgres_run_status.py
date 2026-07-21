#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from idea.config import load_config  # noqa: E402
from idea.database import Database  # noqa: E402
from idea.postgres_corpus import PostgreSQLCorpusPrerequisiteRepository  # noqa: E402


async def synchronize(*, include_nonterminal: bool = False) -> tuple[int, int]:
    config = load_config()
    database = Database(config.storage.database)
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
    repository = PostgreSQLCorpusPrerequisiteRepository(database)
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
    return parser


def main() -> int:
    args = build_parser().parse_args()
    source_count, synchronized = asyncio.run(
        synchronize(include_nonterminal=args.all)
    )
    print(f"source_runs={source_count} synchronized={synchronized}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
