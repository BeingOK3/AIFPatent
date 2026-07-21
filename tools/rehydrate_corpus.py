#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from idea.cache import CacheStore
from idea.config import AppConfig, load_config
from idea.corpus_migration import (
    HistoricalCorpusMigrator,
    MigrationReport,
    RetrievalCorpusFetcher,
)
from idea.database import Database
from idea.providers import ExaMcpProvider, GooglePatentsProvider
from idea.retrieval import RetrievalService
from idea.run_store import RunStore
from idea.runtime import build_corpus_ingest


class RehydrateToolError(RuntimeError):
    """Raised when the migration command cannot be safely executed."""


def safe_error_message(exc: BaseException) -> str:
    message = str(exc)
    for name, value in os.environ.items():
        if (
            value
            and len(value) >= 4
            and any(marker in name.upper() for marker in ("KEY", "PASSWORD", "SECRET", "TOKEN"))
        ):
            message = message.replace(value, "<redacted>")
    return message


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect or rehydrate terminal historical Runs into the durable patent corpus."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="perform writes and provider fetches; omitted means a read-only dry-run",
    )
    parser.add_argument("--run-id", help="limit migration to one historical Run")
    parser.add_argument("--limit", type=int, help="maximum number of Run documents to inspect")
    parser.add_argument("--config", help="application configuration path")
    parser.add_argument("--output", type=Path, help="JSON audit report destination")
    return parser


def report_payload(report: MigrationReport) -> dict[str, object]:
    return {
        "schema_version": 1,
        "mode": "apply" if report.apply else "dry-run",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "counts": report.counts,
        "outcomes": [
            {
                "run_id": outcome.run_id,
                "document_id": outcome.document_id,
                "publication_number": outcome.publication_number,
                "status": outcome.status.value,
                "version_id": outcome.version_id,
                "error_code": outcome.error_code,
            }
            for outcome in report.outcomes
        ],
    }


def write_report(path: Path, payload: dict[str, object]) -> None:
    RunStore.write_json_atomic(path.resolve(), payload)


def _providers(config: AppConfig, cache: CacheStore):
    providers = []
    if config.search.providers.google_patents_local.enabled:
        providers.append(
            GooglePatentsProvider(config.search.providers.google_patents_local, cache=cache)
        )
    if config.search.providers.exa_mcp.enabled:
        providers.append(ExaMcpProvider(config.search.providers.exa_mcp, cache=cache))
    return providers


def build_migrator(config: AppConfig, *, apply: bool) -> HistoricalCorpusMigrator:
    database = Database(config.storage.database)
    if not apply:
        return HistoricalCorpusMigrator(database=database)
    database.initialize()
    cache = CacheStore(
        config.storage.cache_dir,
        database,
        max_bytes=config.storage.cache.max_bytes,
        low_watermark_bytes=config.storage.cache.low_watermark_bytes,
        cleanup_after_write=config.storage.cache.cleanup_after_write,
    )
    cache.repair()
    providers = _providers(config, cache)
    retrieval = RetrievalService(
        database,
        providers,
        search_timeout_seconds={
            "google_patents_local": (
                config.search.providers.google_patents_local.timeout_seconds
            ),
            "exa_mcp": config.search.providers.exa_mcp.timeout_seconds,
        },
        fetch_concurrency=config.workflow.document_agent_concurrency,
    )
    features = config.features.model_copy(update={"patent_corpus": True})
    corpus = build_corpus_ingest(
        config.model_copy(update={"features": features}),
        database=database,
    )
    if corpus is None:
        raise RehydrateToolError("patent corpus adapters were not constructed")
    return HistoricalCorpusMigrator(
        database=database,
        ingest=corpus,
        fetcher=RetrievalCorpusFetcher(retrieval),
    )


def _default_output(config: AppConfig) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return config.storage.runs_dir.parent / "migrations" / f"corpus-rehydrate-{timestamp}.json"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        report = asyncio.run(
            build_migrator(config, apply=args.apply).migrate(
                apply=args.apply,
                run_id=args.run_id,
                limit=args.limit,
            )
        )
        payload = report_payload(report)
        output = args.output or _default_output(config)
        write_report(output, payload)
        print(
            json.dumps(
                {"ok": True, "report": str(output.resolve()), **payload},
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
        )
        return 2 if payload["counts"].get("FAILED", 0) else 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": type(exc).__name__,
                    "message": safe_error_message(exc),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
