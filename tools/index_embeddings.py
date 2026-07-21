#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from idea.config import load_config  # noqa: E402
from idea.postgres_corpus import PostgreSQLPatentChunkRepository  # noqa: E402
from idea.runtime import RuntimeConfigurationError, build_embedding_runtime  # noqa: E402


async def index_chunk_batches(service, chunks, *, batch_size: int = 500) -> int:
    values = tuple(chunks)
    if not values:
        raise RuntimeConfigurationError("embedding backfill requires at least one Chunk")
    if not 1 <= batch_size <= 5000:
        raise RuntimeConfigurationError("Chunk backfill batch size must be between 1 and 5000")
    chunk_ids = tuple(str(item.chunk_id) for item in values)
    if len(set(chunk_ids)) != len(chunk_ids) or any(not value for value in chunk_ids):
        raise RuntimeConfigurationError("embedding backfill Chunk IDs must be unique")
    for start in range(0, len(values), batch_size):
        await service.index_chunks(values[start : start + batch_size])
    await service.activate_for_chunks(chunk_ids)
    return len(values)


async def _ready_version_ids(dsn: str, requested: tuple[str, ...]) -> tuple[str, ...]:
    import psycopg

    connection = await psycopg.AsyncConnection.connect(dsn)
    try:
        async with connection.cursor() as cursor:
            await cursor.execute(
                """
                SELECT DISTINCT c.version_id
                FROM patent_chunks c
                JOIN patent_document_versions v ON v.version_id = c.version_id
                WHERE v.state = 'READY'
                  AND (%s::text[] IS NULL OR c.version_id = ANY(%s))
                ORDER BY c.version_id
                """,
                (list(requested) if requested else None,
                 list(requested) if requested else None),
            )
            values = tuple(str(row[0]) for row in await cursor.fetchall())
    finally:
        await connection.close()
    if requested and set(values) != set(requested):
        missing = sorted(set(requested) - set(values))
        raise RuntimeConfigurationError(
            "requested READY Chunk Version scope is incomplete: " + ", ".join(missing)
        )
    return values


async def run(args: argparse.Namespace) -> dict[str, object]:
    config = load_config(args.config)
    if not config.embedding.enabled:
        raise RuntimeConfigurationError(
            "set embedding.enabled=true before running the deployment backfill"
        )
    runtime = build_embedding_runtime(config)
    if runtime is None:  # pragma: no cover - guarded by enabled setting
        raise RuntimeConfigurationError("embedding runtime was not created")
    dsn = os.environ.get("AIFPATENT_POSTGRES_DSN", "").strip()
    if not dsn:
        raise RuntimeConfigurationError("AIFPATENT_POSTGRES_DSN is required")
    requested = tuple(dict.fromkeys(args.version_id or ()))
    versions = await _ready_version_ids(dsn, requested)
    if not versions:
        raise RuntimeConfigurationError("no READY Corpus Versions have Chunks to index")
    chunks = await PostgreSQLPatentChunkRepository(dsn).list_for_versions(versions)
    indexed = await index_chunk_batches(
        runtime.service, chunks, batch_size=args.chunk_batch_size
    )
    return {
        "ok": True,
        "profile_id": runtime.service.profile.profile_id,
        "provider": runtime.service.profile.provider,
        "model": runtime.service.profile.model,
        "version_count": len(versions),
        "chunk_count": indexed,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill deployment-level embeddings for READY patent Chunks."
    )
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--version-id", action="append")
    parser.add_argument("--chunk-batch-size", type=int, default=500)
    return parser


def main(argv: list[str] | None = None) -> int:
    result = asyncio.run(run(build_parser().parse_args(argv)))
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
