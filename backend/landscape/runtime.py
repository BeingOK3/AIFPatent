from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from idea.config import AppConfig
from idea.model_client import RuntimeModelConfig, StructuredModelClient
from idea.providers import ExaMcpProvider, GooglePatentsProvider

from .database import LandscapeDatabase
from .execution import LandscapeExecutionService
from .reporting import LandscapeReportService
from .store import LandscapeRunStore
from .workflow import LandscapeWorkflow, LandscapeWorkflowHarness


@dataclass
class LandscapeTaskManager:
    database: LandscapeDatabase
    workflow: LandscapeWorkflow
    execution: LandscapeExecutionService

    def __post_init__(self) -> None:
        self.tasks: dict[str, asyncio.Task] = {}
        self._runtime_configs: dict[str, RuntimeModelConfig] = {}

    def start(self, run_id: str, runtime_config: RuntimeModelConfig) -> bool:
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


def build_landscape_runtime(config: AppConfig) -> LandscapeRuntime:
    database_path = config.storage.database.with_name("landscape.db")
    checkpoint_path = config.storage.langgraph_database.with_name("landscape-checkpoints.db")
    runs_dir = config.storage.runs_dir.parent / "landscape-runs"
    database = LandscapeDatabase(database_path)
    database.initialize()
    store = LandscapeRunStore(runs_dir)
    harness = LandscapeWorkflowHarness(
        database,
        store,
        max_step_attempts=config.workflow.max_step_attempts,
    )
    model = StructuredModelClient(config.model)
    providers = []
    timeouts: dict[str, float] = {}
    if config.search.providers.google_patents_local.enabled:
        provider = GooglePatentsProvider(config.search.providers.google_patents_local, cache=None)
        providers.append(provider)
        timeouts[provider.name] = config.search.providers.google_patents_local.timeout_seconds
    if config.search.providers.exa_mcp.enabled:
        provider = ExaMcpProvider(config.search.providers.exa_mcp, cache=None)
        providers.append(provider)
        timeouts[provider.name] = config.search.providers.exa_mcp.timeout_seconds
    report_service = LandscapeReportService(database, store)
    execution = LandscapeExecutionService(
        database=database,
        store=store,
        model=model,
        providers=providers,
        provider_timeout_seconds=timeouts,
        analysis_concurrency=config.workflow.document_agent_concurrency,
        report_service=report_service,
    )
    workflow = LandscapeWorkflow(
        database=database,
        harness=harness,
        checkpoint_path=checkpoint_path,
        step_handler=execution.handle_step,
        limitation_collector=execution.collect_limitations,
        step_timeout_seconds=config.workflow.step_timeout_seconds,
        max_step_attempts=config.workflow.max_step_attempts,
    )
    tasks = LandscapeTaskManager(database, workflow, execution)
    return LandscapeRuntime(config, database, store, harness, workflow, execution, tasks)
