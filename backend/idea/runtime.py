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
from .embeddings import (
    EmbeddingError,
    EmbeddingService,
    OpenAICompatibleEmbeddingProvider,
    ProfiledQueryEmbedding,
)
from .execution import WorkflowExecutor
from .followup_api import FollowupTaskManager
from .followup_context import FollowupContextBuilder
from .followup_handler import FollowupBusinessHandler
from .followup_model import StructuredFollowupModel
from .followup_retrieval import MultiQueryFollowupRetriever
from .followup_workflow import FollowupWorkflow
from .context import ContextAssembler
from .hybrid import HybridRetriever
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
from .postgres_context import PostgreSQLContextRepository
from .postgres_citations import PostgreSQLCitationRepository
from .postgres_lexical import PostgreSQLLexicalSearchRepository
from .postgres_followup import PostgreSQLFollowupRepository
from .postgres_followup_data import PostgreSQLFollowupDataSource
from .postgres_embeddings import PostgreSQLEmbeddingCache
from .postgres_report import PostgreSQLReportScopeRepository
from .postgres_vector import PgVectorIndex
from .providers import ExaMcpProvider, GooglePatentsProvider, SerpApiPatentProvider
from .reporting import ReportService
from .report_rag import InitialReportRagService
from .report_retrieval import InitialReportRetriever
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
    followup_manager: FollowupTaskManager | None = None


@dataclass(frozen=True)
class EmbeddingRuntime:
    service: EmbeddingService
    query: ProfiledQueryEmbedding
    vector: PgVectorIndex


class RuntimeConfigurationError(RuntimeError):
    """Raised when an enabled runtime feature lacks required infrastructure."""


def _build_search_providers(
    config: AppConfig, cache: CacheStore | None
) -> tuple[list, dict[str, float]]:
    providers = []
    timeouts: dict[str, float] = {}
    settings = config.search.providers
    if settings.serpapi_google_patents.enabled:
        provider = SerpApiPatentProvider(settings.serpapi_google_patents, cache=cache)
        providers.append(provider)
        timeouts[provider.name] = settings.serpapi_google_patents.timeout_seconds
    if settings.google_patents_local.enabled:
        provider = GooglePatentsProvider(settings.google_patents_local, cache=cache)
        providers.append(provider)
        timeouts[provider.name] = settings.google_patents_local.timeout_seconds
    if settings.exa_mcp.enabled:
        provider = ExaMcpProvider(settings.exa_mcp, cache=cache)
        providers.append(provider)
        timeouts[provider.name] = settings.exa_mcp.timeout_seconds
    return providers, timeouts


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeConfigurationError(f"{name} is required when patent_corpus is enabled")
    return value


def build_embedding_runtime(config: AppConfig) -> EmbeddingRuntime | None:
    if not config.embedding.enabled:
        return None
    dsn = _required_environment("AIFPATENT_POSTGRES_DSN")
    provider = OpenAICompatibleEmbeddingProvider(config.embedding)
    try:
        provider.api_key()
    except EmbeddingError as exc:
        raise RuntimeConfigurationError(str(exc)) from exc
    service = EmbeddingService(
        provider,
        PostgreSQLEmbeddingCache(dsn),
        normalization=config.embedding.normalization,
        batch_size=config.embedding.batch_size,
    )
    return EmbeddingRuntime(
        service=service,
        query=ProfiledQueryEmbedding(service),
        vector=PgVectorIndex(service.profile, dsn),
    )


def _require_embedding_runtime(
    config: AppConfig, embedding_runtime: EmbeddingRuntime | None
) -> None:
    if config.embedding.enabled and embedding_runtime is None:
        raise RuntimeConfigurationError(
            "enabled embedding requires one shared runtime for indexing and queries"
        )


def build_corpus_ingest(
    config: AppConfig,
    *,
    database: Database | object | None = None,
    embedding_runtime: EmbeddingRuntime | None = None,
) -> PatentCorpusIngestService | None:
    if not config.features.patent_corpus:
        return None
    _require_embedding_runtime(config, embedding_runtime)
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
            repository=PostgreSQLPatentChunkRepository(dsn),
            embedding_indexer=(
                embedding_runtime.service if embedding_runtime is not None else None
            ),
        ),
    )


# Backward-compatible private name for callers from the earlier transition unit.
_build_corpus_ingest = build_corpus_ingest


def build_initial_report_rag(
    config: AppConfig, *, embedding_runtime: EmbeddingRuntime | None = None
) -> InitialReportRagService | None:
    if not config.features.initial_review_rag:
        return None
    _require_embedding_runtime(config, embedding_runtime)
    dsn = _required_environment("AIFPATENT_POSTGRES_DSN")
    chunks = PostgreSQLPatentChunkRepository(dsn)
    lexical = PostgreSQLLexicalSearchRepository(dsn)
    retriever = InitialReportRetriever(
        PostgreSQLReportScopeRepository(dsn),
        lexical,
        chunk_repository=chunks,
        hybrid_search=HybridRetriever(
            lexical,
            config.rag.hybrid,
            vector=(embedding_runtime.vector if embedding_runtime is not None else None),
        ),
        query_embedding=(
            embedding_runtime.query if embedding_runtime is not None else None
        ),
    )
    return InitialReportRagService(retriever, PostgreSQLContextRepository(dsn))


def build_followup_manager(
    config: AppConfig,
    model: StructuredModelClient,
    *,
    embedding_runtime: EmbeddingRuntime | None = None,
) -> FollowupTaskManager | None:
    if not config.features.followup_rag:
        return None
    _require_embedding_runtime(config, embedding_runtime)
    dsn = _required_environment("AIFPATENT_POSTGRES_DSN")
    repository = PostgreSQLFollowupRepository(dsn)
    hybrid = HybridRetriever(
        PostgreSQLLexicalSearchRepository(dsn),
        config.rag.hybrid,
        vector=(embedding_runtime.vector if embedding_runtime is not None else None),
    )
    handler = FollowupBusinessHandler(
        repository=repository,
        data_source=PostgreSQLFollowupDataSource(dsn),
        model=StructuredFollowupModel(model),
        retriever=MultiQueryFollowupRetriever(
            hybrid,
            embedding=(embedding_runtime.query if embedding_runtime is not None else None),
        ),
        context_builder=FollowupContextBuilder(ContextAssembler()),
        context_repository=PostgreSQLContextRepository(dsn),
        system_prompt=(
            "使用简体中文回答。只把本轮 C1..Cn 专利原文作为专利事实证据；"
            "历史对话和首次报告仅用于理解问题，不能替代本轮 Citation。"
        ),
        input_budget=48_000,
        reserved_output_tokens=config.model.max_output_tokens,
    )
    checkpoint = config.storage.langgraph_database.with_name(
        "followup-checkpoints.db"
    )
    workflow = FollowupWorkflow(
        repository=repository,
        handler=handler,
        checkpoint_path=checkpoint,
        step_timeout_seconds=config.workflow.step_timeout_seconds,
        max_step_attempts=config.workflow.max_step_attempts,
    )
    return FollowupTaskManager(repository, workflow)


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
    providers, timeouts = _build_search_providers(config, cache)
    retrieval = RetrievalService(
        database,
        providers,
        search_timeout_seconds=timeouts,
        fetch_concurrency=config.workflow.document_agent_concurrency,
        debug_log=debug_log,
    )
    citation_repository = None
    if config.features.initial_review_rag:
        citation_repository = PostgreSQLCitationRepository(
            _required_environment("AIFPATENT_POSTGRES_DSN")
        )
    documents = DocumentAnalysisService(
        database,
        agents,
        concurrency=config.workflow.document_agent_concurrency,
        citations=citation_repository,
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
    reporting = ReportService(
        database, run_store, agents, citations=citation_repository
    )
    embedding_runtime = build_embedding_runtime(config)
    corpus_ingest = build_corpus_ingest(
        config, database=database, embedding_runtime=embedding_runtime
    )
    report_rag = build_initial_report_rag(
        config, embedding_runtime=embedding_runtime
    )
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
        report_rag=report_rag,
    )
    followup_manager = build_followup_manager(
        config, model, embedding_runtime=embedding_runtime
    )
    return IdeaRuntime(
        config, database, cache, run_store, harness, executor, debug_log,
        followup_manager,
    )
