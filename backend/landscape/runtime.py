from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path

from idea.config import AppConfig
from idea.model_client import RuntimeModelConfig, StructuredModelClient
from idea.cache import CacheStore
from idea.providers import (
    ExaMcpProvider,
    GooglePatentsProvider,
    SerpApiPatentProvider,
)
from idea.providers.base import PagedSearchProvider

from .database import LandscapeDatabase
from .company_workflow import LandscapeCompanyFanout
from .execution import LandscapeExecutionService
from .postgres_database import LandscapePostgreSQLDatabase
from .reporting import LandscapeReportService
from .store import LandscapeRunStore
from .scope_expansion import ScopeExpansionService
from .scope_repository import PostgreSQLScopeDraftRepository
from .scope_service import ScopeDraftPreparationService
from .run_repository import PostgreSQLLandscapeRunRepository
from .query_repository import PostgreSQLQueryPlanRepository
from .search_page_repository import PostgreSQLSearchPageRepository
from .search_execution import PagedSearchExecutionService
from .publication_repository import PostgreSQLPublicationRepository
from .scale_repository import PostgreSQLScaleGateRepository
from .search_coordinator import LandscapeSearchCoordinator
from .abstract_repository import PostgreSQLAbstractEvidenceRepository
from .task_queue import PostgreSQLTaskQueue
from .model_scheduler import ModelBudget, ModelScheduler
from .credential_lease import CredentialVault
from .batching import make_profile
from .direction_agent import DirectionExtractionService
from .classification_agent import ClassificationMatchingService
from .semantic_result_repository import (
    PostgreSQLClassificationRepository,
    PostgreSQLDirectionRepository,
)
from .others_repository import PostgreSQLOthersRepository
from .analytics_repository import (
    PostgreSQLMetricRepository,
    PostgreSQLRepresentativeRepository,
    PostgreSQLTrendRepository,
)
from .stage_repository import PostgreSQLStageRepository
from .family_repository import PostgreSQLFamilyRepository
from .organization_repository import PostgreSQLOrganizationRepository
from .patent_snapshot_repository import PostgreSQLPatentSnapshotRepository
from .patent_snapshot_fetch import PatentSnapshotFetchService
from .taxonomy import TaxonomyArtifact
from .taxonomy_repository import PostgreSQLTaxonomyRepository
from .workflow import LandscapeWorkflow, LandscapeWorkflowHarness
from .v4_workflow import V4LandscapeWorkflow


@dataclass
class LandscapeTaskManager:
    database: LandscapeDatabase
    workflow: LandscapeWorkflow
    execution: LandscapeExecutionService

    def __post_init__(self) -> None:
        self.tasks: dict[str, asyncio.Task] = {}
        self._runtime_configs: dict[str, RuntimeModelConfig] = {}

    def start(
        self,
        run_id: str,
        runtime_config: RuntimeModelConfig,
    ) -> bool:
        run = self.database.get_run(run_id)
        if run["status"] in {"COMPLETED", "COMPLETED_WITH_LIMITATIONS", "FAILED", "CANCELLED"}:
            return False
        current = self.tasks.get(run_id)
        if current and not current.done():
            return False
        self._runtime_configs[run_id] = runtime_config
        self.tasks[run_id] = asyncio.create_task(self._run(run_id), name=f"landscape-run:{run_id}")
        return True

    async def _run(self, run_id: str) -> None:
        try:
            config = self._runtime_configs.get(run_id)
            if config is None:
                self.database.set_run_status(
                    run_id,
                    "FAILED",
                    error_code="RUNTIME_API_KEY_REQUIRED",
                    error_message="本次 Run 的临时 API Token 已不可用，请重新输入后重跑。",
                )
                return
            from idea.model_client import runtime_model_config

            with runtime_model_config(config):
                await self.workflow.execute(run_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            try:
                if self.database.get_run(run_id)["status"] in {"QUEUED", "RUNNING"}:
                    self.database.set_run_status(
                        run_id, "FAILED", error_code=type(exc).__name__, error_message=str(exc)[:2000]
                    )
            except KeyError:
                pass
        finally:
            if self.tasks.get(run_id) is asyncio.current_task():
                self.tasks.pop(run_id, None)
            self._runtime_configs.pop(run_id, None)

    async def cancel(self, run_id: str) -> bool:
        run = self.database.get_run(run_id)
        if run["status"] in {"COMPLETED", "COMPLETED_WITH_LIMITATIONS", "FAILED", "CANCELLED"}:
            return False
        task = self.tasks.get(run_id)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if self.database.get_run(run_id)["status"] not in {"COMPLETED", "COMPLETED_WITH_LIMITATIONS", "FAILED", "CANCELLED"}:
            self.workflow.harness.cancel_run(run_id)
        self._runtime_configs.pop(run_id, None)
        return True

    def resume_incomplete(self) -> int:
        return self.database.mark_interrupted_runs_failed()

    async def aclose(self) -> None:
        for run_id in list(self.tasks):
            await self.cancel(run_id)
        await self.workflow.aclose()


@dataclass(frozen=True)
class LandscapeRuntime:
    config: AppConfig
    database: LandscapeDatabase
    store: LandscapeRunStore
    harness: LandscapeWorkflowHarness
    workflow: LandscapeWorkflow
    execution: LandscapeExecutionService
    tasks: LandscapeTaskManager
    taxonomy_repository: PostgreSQLTaxonomyRepository
    taxonomy: TaxonomyArtifact
    scope_repository: PostgreSQLScopeDraftRepository
    scope_service: ScopeDraftPreparationService
    run_repository: PostgreSQLLandscapeRunRepository
    query_repository: PostgreSQLQueryPlanRepository
    search_page_repository: PostgreSQLSearchPageRepository
    search_execution: PagedSearchExecutionService
    publication_repository: PostgreSQLPublicationRepository
    scale_repository: PostgreSQLScaleGateRepository
    search_coordinator: LandscapeSearchCoordinator
    abstract_repository: PostgreSQLAbstractEvidenceRepository
    direction_repository: PostgreSQLDirectionRepository
    classification_repository: PostgreSQLClassificationRepository
    others_repository: PostgreSQLOthersRepository
    metric_repository: PostgreSQLMetricRepository
    v4_trend_repository: PostgreSQLTrendRepository
    representative_repository: PostgreSQLRepresentativeRepository
    stage_repository: PostgreSQLStageRepository
    family_repository: PostgreSQLFamilyRepository
    organization_repository: PostgreSQLOrganizationRepository
    patent_snapshot_repository: PostgreSQLPatentSnapshotRepository
    patent_snapshot_fetch: PatentSnapshotFetchService | None
    v4_workflow: V4LandscapeWorkflow | None
    task_queue: PostgreSQLTaskQueue
    model_scheduler: ModelScheduler
    direction_extraction: DirectionExtractionService
    classification_matching: ClassificationMatchingService
    credential_vault: CredentialVault


def build_landscape_runtime(
    config: AppConfig, *, cache: CacheStore | None = None
) -> LandscapeRuntime:
    dsn = os.environ.get("AIFPATENT_POSTGRES_DSN", "").strip()
    if not dsn:
        raise RuntimeError(
            "AIFPATENT_POSTGRES_DSN is required for Landscape PostgreSQL persistence"
        )
    runs_dir = config.storage.runs_dir.parent / "landscape-runs"
    database = LandscapePostgreSQLDatabase(dsn)
    database.initialize()
    taxonomy_repository = PostgreSQLTaxonomyRepository(dsn)
    taxonomy = taxonomy_repository.ensure_current(
        Path(__file__).resolve().parents[2]
        / "development"
        / "landscape"
        / "classify.md"
    )
    scope_repository = PostgreSQLScopeDraftRepository(dsn)
    run_repository = PostgreSQLLandscapeRunRepository(
        dsn,
        scope_repository,
        taxonomy_repository,
    )
    query_repository = PostgreSQLQueryPlanRepository(dsn)
    search_page_repository = PostgreSQLSearchPageRepository(dsn)
    search_execution = PagedSearchExecutionService(
        max_concurrency=min(8, config.workflow.document_agent_concurrency),
        page_size=100,
    )
    publication_repository = PostgreSQLPublicationRepository(dsn)
    scale_repository = PostgreSQLScaleGateRepository(dsn)
    search_coordinator = LandscapeSearchCoordinator(
        query_repository=query_repository,
        page_repository=search_page_repository,
        scale_repository=scale_repository,
        publication_repository=publication_repository,
        execution=search_execution,
    )
    abstract_repository = PostgreSQLAbstractEvidenceRepository(dsn)
    direction_repository = PostgreSQLDirectionRepository(dsn)
    classification_repository = PostgreSQLClassificationRepository(dsn)
    others_repository = PostgreSQLOthersRepository(dsn)
    metric_repository = PostgreSQLMetricRepository(dsn)
    v4_trend_repository = PostgreSQLTrendRepository(dsn)
    representative_repository = PostgreSQLRepresentativeRepository(dsn)
    stage_repository = PostgreSQLStageRepository(dsn)
    family_repository = PostgreSQLFamilyRepository(dsn)
    organization_repository = PostgreSQLOrganizationRepository(dsn)
    patent_snapshot_repository = PostgreSQLPatentSnapshotRepository(dsn)
    task_queue = PostgreSQLTaskQueue(dsn)
    model_scheduler = ModelScheduler(
        ModelBudget(
            max_concurrency=min(8, config.workflow.document_agent_concurrency),
            rpm=_int_env("AIFPATENT_MODEL_RPM", 60, minimum=1),
            tpm=_int_env("AIFPATENT_MODEL_TPM", 200_000, minimum=1),
            max_input_tokens=_int_env("AIFPATENT_MODEL_MAX_INPUT_TOKENS", 600_000, minimum=1),
            max_output_tokens=_int_env("AIFPATENT_MODEL_MAX_OUTPUT_TOKENS", 8_192, minimum=1),
        )
    )
    credential_vault = CredentialVault()
    store = LandscapeRunStore(runs_dir)
    harness = LandscapeWorkflowHarness(
        database,
        store,
        max_step_attempts=config.workflow.max_step_attempts,
    )
    model = StructuredModelClient(config.model)
    bulk_profile = make_profile(
        model_role="bulk",
        verified_context_tokens=_int_env(
            "AIFPATENT_MODEL_VERIFIED_CONTEXT_TOKENS",
            600_000,
            minimum=10_000,
        ),
        safety_ratio=0.65,
        fixed_prompt_tokens=20_000,
        reserved_output_tokens=min(8_192, config.model.max_output_tokens),
        max_batch_input_tokens=_int_env(
            "AIFPATENT_MODEL_MAX_BATCH_INPUT_TOKENS",
            400_000,
            minimum=1_000,
        ),
        max_batch_output_tokens=min(8_192, config.model.max_output_tokens),
        max_batch_items=_int_env(
            "AIFPATENT_MODEL_MAX_BATCH_ITEMS", 8, minimum=1
        ),
        max_taxonomy_candidates=500,
    )
    direction_extraction = DirectionExtractionService(
        model,
        model_scheduler,
        bulk_profile,
        batch_retries=max(0, config.workflow.max_step_attempts - 1),
    )
    classification_matching = ClassificationMatchingService(
        model,
        model_scheduler,
        bulk_profile,
        batch_retries=max(0, config.workflow.max_step_attempts - 1),
    )
    scope_service = ScopeDraftPreparationService(
        scope_repository,
        ScopeExpansionService(model),
        max_company_concurrency=min(8, config.workflow.document_agent_concurrency),
        object_timeout_seconds=min(90, config.model.timeout_seconds),
    )
    providers = []
    timeouts: dict[str, float] = {}
    if config.search.providers.serpapi_google_patents.enabled:
        provider = SerpApiPatentProvider(
            config.search.providers.serpapi_google_patents, cache=cache
        )
        providers.append(provider)
        timeouts[provider.name] = config.search.providers.serpapi_google_patents.timeout_seconds
    if config.search.providers.google_patents_local.enabled:
        provider = GooglePatentsProvider(config.search.providers.google_patents_local, cache=cache)
        providers.append(provider)
        timeouts[provider.name] = config.search.providers.google_patents_local.timeout_seconds
    if config.search.providers.exa_mcp.enabled:
        provider = ExaMcpProvider(config.search.providers.exa_mcp, cache=cache)
        providers.append(provider)
        timeouts[provider.name] = config.search.providers.exa_mcp.timeout_seconds
    patent_snapshot_fetch = (
        PatentSnapshotFetchService(
            providers,
            patent_snapshot_repository,
            max_concurrency=min(8, config.workflow.document_agent_concurrency),
            timeout_seconds=min(90, config.model.timeout_seconds),
            max_attempts_per_provider=config.workflow.max_step_attempts,
        )
        if providers
        else None
    )
    paged_provider = next(
        (provider for provider in providers if isinstance(provider, PagedSearchProvider)),
        None,
    )
    v4_workflow = (
        V4LandscapeWorkflow(
            run_repository=run_repository,
            scope_repository=scope_repository,
            stage_repository=stage_repository,
            search_coordinator=search_coordinator,
            publication_repository=publication_repository,
            snapshot_fetch=patent_snapshot_fetch,
            snapshot_repository=patent_snapshot_repository,
            family_repository=family_repository,
            abstract_repository=abstract_repository,
            organization_repository=organization_repository,
            provider=paged_provider,
            direction_extraction=direction_extraction,
            classification_matching=classification_matching,
            direction_repository=direction_repository,
            classification_repository=classification_repository,
            taxonomy_repository=taxonomy_repository,
            others_repository=others_repository,
        )
        if patent_snapshot_fetch is not None and paged_provider is not None
        else None
    )
    report_service = LandscapeReportService(database, store)
    execution = LandscapeExecutionService(
        database=database,
        store=store,
        model=model,
        providers=providers,
        provider_timeout_seconds=timeouts,
        analysis_concurrency=config.workflow.document_agent_concurrency,
        report_service=report_service,
        candidate_repository=database,
        company_repository=database,
        fetch_repository=database,
        analysis_repository=database,
        direction_evidence_repository=database,
        profile_repository=database,
        trend_repository=database,
        audit_repository=database,
    )
    company_fanout = LandscapeCompanyFanout(
        harness=harness,
        execution=execution,
    )
    execution.bind_company_fanout(company_fanout)
    workflow = LandscapeWorkflow(
        database=database,
        harness=harness,
        step_handler=execution.handle_step,
        limitation_collector=execution.collect_limitations,
        step_timeout_seconds=config.workflow.step_timeout_seconds,
        max_step_attempts=config.workflow.max_step_attempts,
    )
    tasks = LandscapeTaskManager(database, workflow, execution)
    return LandscapeRuntime(
        config,
        database,
        store,
        harness,
        workflow,
        execution,
        tasks,
        taxonomy_repository,
        taxonomy,
        scope_repository,
        scope_service,
        run_repository,
        query_repository,
        search_page_repository,
        search_execution,
        publication_repository,
        scale_repository,
        search_coordinator,
        abstract_repository,
        direction_repository,
        classification_repository,
        others_repository,
        metric_repository,
        v4_trend_repository,
        representative_repository,
        stage_repository,
        family_repository,
        organization_repository,
        patent_snapshot_repository,
        patent_snapshot_fetch,
        v4_workflow,
        task_queue,
        model_scheduler,
        direction_extraction,
        classification_matching,
        credential_vault,
    )


def _int_env(name: str, default: int, *, minimum: int) -> int:
    value = os.environ.get(name, str(default)).strip()
    try:
        parsed = int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if parsed < minimum:
        raise RuntimeError(f"{name} must be >= {minimum}")
    return parsed
