from __future__ import annotations

import os
from dataclasses import dataclass

from .agents import IdeaAgentService
from .audit import AuditService
from .cache import CacheStore
from .chunks import PatentChunkPersistenceService
from .config import AppConfig
from .corpus import PatentCorpusIngestService, PatentCorpusService
from .database import Database
from .document_analysis import DocumentAnalysisService
from .execution import WorkflowExecutor
from .inventiveness import InventivenessService
from .model_client import StructuredModelClient
from .novelty import NoveltyService
from .postgres_corpus import (
    PostgreSQLCorpusPrerequisiteRepository,
    PostgreSQLCorpusRunLinkRepository,
    PostgreSQLCorpusVersionSourceRepository,
    PostgreSQLCorpusVersionRepository,
    PostgreSQLPatentChunkRepository,
)
from .providers import ExaMcpProvider, GooglePatentsProvider
from .reporting import ReportService
from .retrieval import RetrievalService
from .run_store import RunStore
from .runtime_debug import RunDebugLog
from .s3_object_store import S3ObjectStore
from .value_analysis import ValueAnalysisService
from .workflow import WorkflowHarness


@dataclass(frozen=True)
class IdeaRuntime:
    config: AppConfig
    database: Database
    cache: CacheStore
    run_store: RunStore
    harness: WorkflowHarness
    executor: WorkflowExecutor
    debug_log: RunDebugLog


class RuntimeConfigurationError(RuntimeError):
    """Raised when an enabled runtime feature lacks required infrastructure."""


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeConfigurationError(f"{name} is required when patent_corpus is enabled")
    return value


def _build_corpus_ingest(
    config: AppConfig, *, database: Database | object | None = None
) -> PatentCorpusIngestService | None:
    if not config.features.patent_corpus:
        return None
    dsn = _required_environment("AIFPATENT_POSTGRES_DSN")
    if database is None:
        raise RuntimeConfigurationError(
            "SQLite source database is required for the PostgreSQL Corpus transition"
        )
    objects = S3ObjectStore(
        endpoint_url=_required_environment("AIFPATENT_S3_ENDPOINT_URL"),
        bucket=_required_environment("AIFPATENT_S3_BUCKET"),
        access_key=_required_environment("AIFPATENT_S3_ACCESS_KEY"),
        secret_key=_required_environment("AIFPATENT_S3_SECRET_KEY"),
        region_name=os.environ.get("AIFPATENT_S3_REGION", "us-east-1"),
    )
    corpus = PatentCorpusService(
        versions=PostgreSQLCorpusVersionRepository(dsn),
        objects=objects,
        sources=PostgreSQLCorpusVersionSourceRepository(dsn),
    )
    return PatentCorpusIngestService(
        corpus=corpus,
        run_links=PostgreSQLCorpusRunLinkRepository(dsn),
        prerequisites=PostgreSQLCorpusPrerequisiteRepository(database, dsn),
        chunk_persistence=PatentChunkPersistenceService(
            repository=PostgreSQLPatentChunkRepository(dsn)
        ),
    )


def build_runtime(config: AppConfig) -> IdeaRuntime:
    database = Database(config.storage.database)
    database.initialize()
    cache = CacheStore(
        config.storage.cache_dir,
        database,
        max_bytes=config.storage.cache.max_bytes,
        low_watermark_bytes=config.storage.cache.low_watermark_bytes,
        cleanup_after_write=config.storage.cache.cleanup_after_write,
    )
    cache.repair()
    run_store = RunStore(config.storage.runs_dir)
    debug_log = RunDebugLog(config.storage.runs_dir.parent / "debug" / "idea-runs")
    harness = WorkflowHarness(
        database,
        run_store,
        max_step_attempts=config.workflow.max_step_attempts,
    )
    harness.recover_incomplete()

    model = StructuredModelClient(config.model)
    agents = IdeaAgentService(database, model, debug_log=debug_log)
    providers = []
    if config.search.providers.google_patents_local.enabled:
        providers.append(
            GooglePatentsProvider(config.search.providers.google_patents_local, cache=cache)
        )
    if config.search.providers.exa_mcp.enabled:
        providers.append(ExaMcpProvider(config.search.providers.exa_mcp, cache=cache))
    timeouts = {
        "google_patents_local": config.search.providers.google_patents_local.timeout_seconds,
        "exa_mcp": config.search.providers.exa_mcp.timeout_seconds,
    }
    retrieval = RetrievalService(
        database,
        providers,
        search_timeout_seconds=timeouts,
        fetch_concurrency=config.workflow.document_agent_concurrency,
        debug_log=debug_log,
    )
    documents = DocumentAnalysisService(
        database,
        agents,
        concurrency=config.workflow.document_agent_concurrency,
    )
    minimum = min(
        config.search.modes.quick.deep_review_min,
        config.search.modes.standard.deep_review_min,
        config.search.modes.deep.deep_review_min,
    )
    novelty = NoveltyService(database, minimum_deep_reviews=minimum)
    inventiveness = InventivenessService(
        database,
        agents,
        concurrency=config.workflow.inventive_route_concurrency,
    )
    value = ValueAnalysisService(database, agents)
    audit = AuditService(database, agents, minimum_deep_reviews=minimum)
    reporting = ReportService(database, run_store, agents)
    corpus_ingest = _build_corpus_ingest(config, database=database)
    executor = WorkflowExecutor(
        config,
        database,
        run_store,
        harness,
        agents,
        retrieval,
        documents,
        novelty,
        inventiveness,
        value,
        audit,
        reporting,
        debug_log=debug_log,
        corpus_ingest=corpus_ingest,
    )
    return IdeaRuntime(config, database, cache, run_store, harness, executor, debug_log)
