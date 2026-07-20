from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from datetime import date
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr, field_validator, model_validator

from .config import AppConfig, SearchMode
from .database import Database
from .execution import WorkflowExecutor
from .model_client import RuntimeModelConfig, runtime_model_config
from .run_store import RunStore
from .runtime_debug import RunDebugLog
from .workflow import TERMINAL_RUN_STATUSES, WorkflowHarness


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateCaseRequest(ApiModel):
    title: str = Field(min_length=1, max_length=200)


class RunSettings(ApiModel):
    search_mode: Literal["quick", "standard", "deep"] = "standard"
    candidate_max: int | None = Field(default=None, ge=10, le=500)
    deep_review_min: int | None = Field(default=None, ge=10, le=100)
    deep_review_max: int | None = Field(default=None, ge=10, le=100)

    @model_validator(mode="after")
    def coherent_limits(self) -> "RunSettings":
        if (
            self.deep_review_min is not None
            and self.deep_review_max is not None
            and self.deep_review_max < self.deep_review_min
        ):
            raise ValueError("deep_review_max must be >= deep_review_min")
        if (
            self.candidate_max is not None
            and self.deep_review_max is not None
            and self.candidate_max < self.deep_review_max
        ):
            raise ValueError("candidate_max must be >= deep_review_max")
        return self


class RuntimeModelRequest(ApiModel):
    api_key: SecretStr
    base_url: HttpUrl
    model: str = Field(min_length=1, max_length=200)

    @field_validator("api_key")
    @classmethod
    def nonempty_api_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("api_key must not be blank")
        return value

    @field_validator("model")
    @classmethod
    def normalized_model(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("model must not be blank")
        return value

    @field_validator("base_url")
    @classmethod
    def safe_base_url(cls, value: HttpUrl) -> HttpUrl:
        if value.username or value.password or value.query or value.fragment:
            raise ValueError("base_url must not contain credentials, query, or fragment")
        return value

    def runtime_config(self) -> RuntimeModelConfig:
        return RuntimeModelConfig(
            base_url=str(self.base_url).rstrip("/"),
            api_key=self.api_key.get_secret_value(),
            model=self.model,
        )


class CreateRunRequest(RuntimeModelRequest):
    input_text: str = Field(min_length=10, max_length=200_000)
    evaluation_date: date = Field(default_factory=date.today)
    date_basis: str = Field(default="用户指定或提交日", min_length=1, max_length=200)
    analysis_scope: Literal["full"] = "full"
    settings: RunSettings = Field(default_factory=RunSettings)
    attachment_names: list[str] = Field(default_factory=list, max_length=20)


class DeleteRequest(ApiModel):
    operator_label: str | None = Field(default=None, max_length=100)


class RunTaskManager:
    def __init__(
        self,
        database: Database,
        harness: WorkflowHarness,
        executor: WorkflowExecutor,
        debug_log: RunDebugLog | None = None,
    ):
        self.database = database
        self.harness = harness
        self.executor = executor
        self.debug_log = debug_log
        self.tasks: dict[str, asyncio.Task] = {}
        self._runtime_configs: dict[str, RuntimeModelConfig] = {}

    def start(self, run_id: str, *, runtime_config: RuntimeModelConfig | None = None) -> bool:
        run = self.database.get_run(run_id)
        if run["status"] in TERMINAL_RUN_STATUSES:
            return False
        existing = self.tasks.get(run_id)
        if existing and not existing.done():
            return False
        value = runtime_config or self._runtime_configs.get(run_id)
        if value is None:
            return False
        self._runtime_configs[run_id] = value
        if self.debug_log:
            self.debug_log.append(
                run_id,
                "run_scheduled",
                model=value.model,
                base_url=value.base_url,
            )
        task = asyncio.create_task(self._run(run_id), name=f"idea-run:{run_id}")
        self.tasks[run_id] = task
        return True

    async def _run(self, run_id: str) -> None:
        try:
            config = self._runtime_configs.get(run_id)
            if not config:
                self.database.set_run_status(
                    run_id,
                    "FAILED",
                    error_code="RUNTIME_API_KEY_REQUIRED",
                    error_message="本次 Run 的临时 API Token 已不可用，请重新输入后重跑。",
                )
                return
            with runtime_model_config(config):
                await self.executor.execute(run_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            run = self.database.get_run(run_id)
            if run["status"] in {"QUEUED", "RUNNING"}:
                self.database.set_run_status(
                    run_id,
                    "FAILED",
                    error_code=type(exc).__name__,
                    error_message=str(exc),
                )
            if self.debug_log:
                self.debug_log.append(
                    run_id,
                    "run_failed",
                    error_code=type(exc).__name__,
                    error_message=str(exc)[:1000],
                )
        finally:
            current = self.tasks.get(run_id)
            if current is asyncio.current_task():
                self.tasks.pop(run_id, None)
            self._runtime_configs.pop(run_id, None)

    async def cancel(self, run_id: str) -> bool:
        run = self.database.get_run(run_id)
        if run["status"] in TERMINAL_RUN_STATUSES:
            return False
        task = self.tasks.get(run_id)
        if task and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        run = self.database.get_run(run_id)
        if run["status"] not in TERMINAL_RUN_STATUSES:
            self.harness.cancel_run(run_id)
        self._runtime_configs.pop(run_id, None)
        if self.debug_log:
            self.debug_log.append(run_id, "run_cancelled")
        return True

    def resume_incomplete(self) -> list[str]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT run_id FROM idea_runs WHERE status IN ('QUEUED','RUNNING') ORDER BY created_at"
            ).fetchall()
        failed = []
        for row in rows:
            self.database.set_run_status(
                row["run_id"],
                "FAILED",
                error_code="RUNTIME_API_KEY_REQUIRED_AFTER_RESTART",
                error_message="服务已重启，临时 API Token 未被保存；请在页面重新输入后重跑。",
            )
            failed.append(row["run_id"])
        return failed


def _run_config_snapshot(config: AppConfig, request: RuntimeModelRequest) -> dict[str, Any]:
    snapshot = config.snapshot()
    snapshot["model"] = {
        **snapshot["model"],
        "default": request.model,
        "base_url": str(request.base_url).rstrip("/"),
        "credential_source": "per_run_memory",
    }
    return snapshot


def create_idea_router(
    config: AppConfig,
    database: Database,
    run_store: RunStore,
    harness: WorkflowHarness,
    manager: RunTaskManager,
    debug_log: RunDebugLog | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/idea", tags=["IDEA"])

    @router.post("/cases")
    async def create_case(request: CreateCaseRequest):
        try:
            return database.create_case(request.title)
        except ValueError as exc:
            if "already exists" in str(exc):
                raise HTTPException(409, "Case 名称已存在，请使用唯一、可辨识的方案组名称")
            raise HTTPException(422, "Case 名称不能为空")

    @router.get("/cases")
    async def list_cases():
        return {"cases": database.list_cases()}

    @router.get("/cases/{case_id}")
    async def get_case(case_id: str):
        try:
            return database.get_case(case_id)
        except KeyError:
            raise HTTPException(404, "case not found")

    @router.post("/cases/{case_id}/runs")
    async def create_run(case_id: str, request: CreateRunRequest):
        _validate_run_settings(config, request.settings)
        attachments = _attachments(config, request.attachment_names)
        try:
            run = database.create_run(
                case_id=case_id,
                input_text=request.input_text,
                evaluation_date=request.evaluation_date.isoformat(),
                date_basis=request.date_basis,
                analysis_scope=request.analysis_scope,
                model=request.model,
                skill_version="patent-idea-review/2.0.0",
                workflow_version="idea-workflow/2.0.0",
                config_snapshot=_run_config_snapshot(config, request),
                attachments=attachments,
                settings=request.settings.model_dump(mode="json", exclude_none=True),
            )
        except KeyError:
            raise HTTPException(404, "case not found")
        manager.start(run["run_id"], runtime_config=request.runtime_config())
        return _run_view(database, harness, run["run_id"])

    @router.get("/runs/{run_id}")
    async def get_run(run_id: str):
        try:
            return _run_view(database, harness, run_id)
        except KeyError:
            raise HTTPException(404, "run not found")

    @router.get("/runs/{run_id}/debug")
    async def get_run_debug(run_id: str):
        try:
            run_view = _run_view(database, harness, run_id)
        except KeyError:
            raise HTTPException(404, "run not found")
        with database.connect() as connection:
            step_rows = connection.execute(
                """SELECT step_name,attempt,status,input_hash,output_hash,error_code,
                          error_message,started_at,completed_at
                   FROM run_steps WHERE run_id = ? ORDER BY step_id""",
                (run_id,),
            ).fetchall()
            call_rows = connection.execute(
                """SELECT call_id,step_name,provider,operation,request_json,
                          response_summary_json,result_count,duration_ms,status,
                          error_code,error_message,created_at
                   FROM tool_calls WHERE run_id = ? ORDER BY created_at,call_id""",
                (run_id,),
            ).fetchall()
        tool_calls = []
        for row in call_rows:
            item = dict(row)
            item["request"] = _safe_json(item.pop("request_json"), {})
            item["response_summary"] = _safe_json(
                item.pop("response_summary_json"), None
            )
            tool_calls.append(RunDebugLog.sanitize(item))
        return {
            "run": run_view,
            "steps": [RunDebugLog.sanitize(dict(row)) for row in step_rows],
            "tool_calls": tool_calls,
            "events": debug_log.read(run_id) if debug_log else [],
            "log_storage": {
                "git_ignored": True,
                "format": "jsonl",
                "file": f"workspace/debug/idea-runs/{run_id}.jsonl",
            },
        }

    @router.get("/runs/{run_id}/events")
    async def run_events(run_id: str):
        try:
            database.get_run(run_id)
        except KeyError:
            raise HTTPException(404, "run not found")

        async def events():
            previous = None
            while True:
                try:
                    view = _run_view(database, harness, run_id)
                except KeyError:
                    yield _sse({"type": "deleted", "run_id": run_id})
                    return
                encoded = json.dumps(view, ensure_ascii=False, sort_keys=True)
                if encoded != previous:
                    yield _sse({"type": "progress", "data": view})
                    previous = encoded
                if view["status"] in TERMINAL_RUN_STATUSES:
                    yield _sse({"type": "terminal", "data": view})
                    return
                await asyncio.sleep(0.5)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.post("/runs/{run_id}/cancel")
    async def cancel_run(run_id: str):
        try:
            cancelled = await manager.cancel(run_id)
        except KeyError:
            raise HTTPException(404, "run not found")
        return {"run_id": run_id, "cancelled": cancelled, "status": database.get_run(run_id)["status"]}

    @router.post("/runs/{run_id}/rerun")
    async def rerun(run_id: str, request: RuntimeModelRequest):
        try:
            source = database.get_run(run_id)
        except KeyError:
            raise HTTPException(404, "run not found")
        rerun_attachments = []
        source_paths = run_store.paths(source["case_id"], run_id)
        for attachment in source["attachments_json"]:
            name = attachment.get("name") if isinstance(attachment, dict) else None
            copied = source_paths.input_dir / name if name else None
            if copied and copied.is_file():
                rerun_attachments.append(
                    {"name": name, "stored_path": str(copied), "size_bytes": copied.stat().st_size}
                )
        new_run = database.create_run(
            case_id=source["case_id"],
            input_text=source["input_text"],
            evaluation_date=source["evaluation_date"],
            date_basis=source["date_basis"],
            analysis_scope=source["analysis_scope"],
            model=request.model,
            skill_version="patent-idea-review/2.0.0",
            workflow_version="idea-workflow/2.0.0",
            config_snapshot=_run_config_snapshot(config, request),
            attachments=rerun_attachments,
            settings=source["settings_json"],
            parent_run_id=run_id,
        )
        manager.start(new_run["run_id"], runtime_config=request.runtime_config())
        return _run_view(database, harness, new_run["run_id"])

    @router.get("/runs/{run_id}/report")
    async def get_report(run_id: str):
        try:
            run = database.get_run(run_id)
        except KeyError:
            raise HTTPException(404, "run not found")
        with database.connect() as connection:
            row = connection.execute(
                "SELECT report_json_path FROM reports WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise HTTPException(404, "report not available")
        try:
            run_store.verify(run["case_id"], run_id)
            return json.loads(Path(row["report_json_path"]).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, RuntimeError):
            raise HTTPException(409, "stored report failed integrity loading")

    @router.get("/runs/{run_id}/report.md")
    async def download_markdown(run_id: str):
        try:
            run = database.get_run(run_id)
        except KeyError:
            raise HTTPException(404, "run not found")
        with database.connect() as connection:
            row = connection.execute(
                "SELECT report_md_path FROM reports WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None or not Path(row["report_md_path"]).is_file():
            raise HTTPException(404, "markdown report not available")
        try:
            run_store.verify(run["case_id"], run_id)
        except RuntimeError:
            raise HTTPException(409, "stored report failed integrity loading")
        return FileResponse(row["report_md_path"], filename=f"idea-{run_id}.md")

    @router.get("/runs/{run_id}/artifacts")
    async def list_artifacts(run_id: str):
        try:
            run = database.get_run(run_id)
        except KeyError:
            raise HTTPException(404, "run not found")
        paths = run_store.paths(run["case_id"], run_id)
        files = []
        if paths.root.exists():
            for path in sorted(item for item in paths.root.rglob("*") if item.is_file()):
                files.append({
                    "path": str(path.relative_to(paths.root)),
                    "size_bytes": path.stat().st_size,
                })
        return {"run_id": run_id, "files": files}

    @router.delete("/runs/{run_id}")
    async def delete_run(run_id: str, request: DeleteRequest | None = None):
        try:
            run = database.get_run(run_id)
        except KeyError:
            raise HTTPException(404, "run not found")
        await manager.cancel(run_id)
        run_store.delete_run(run["case_id"], run_id)
        database.delete_run(run_id, request.operator_label if request else None)
        return {"deleted": run_id}

    @router.delete("/cases/{case_id}")
    async def delete_case(case_id: str, request: DeleteRequest | None = None):
        try:
            case = database.get_case(case_id)
        except KeyError:
            raise HTTPException(404, "case not found")
        for run in case["runs"]:
            await manager.cancel(run["run_id"])
        run_store.delete_case(case_id)
        database.delete_case(case_id, request.operator_label if request else None)
        return {"deleted": case_id, "run_count": len(case["runs"])}

    return router


def _attachments(config: AppConfig, names: list[str]) -> list[dict[str, Any]]:
    records = []
    seen = set()
    uploads = config.storage.uploads_dir.resolve()
    for raw_name in names:
        name = Path(raw_name).name
        if name != raw_name or name in seen:
            raise HTTPException(422, "invalid or duplicate attachment name")
        seen.add(name)
        path = (uploads / name).resolve()
        if uploads not in path.parents or not path.is_file():
            raise HTTPException(422, f"attachment not found: {name}")
        records.append({"name": name, "stored_path": str(path), "size_bytes": path.stat().st_size})
    return records


def _validate_run_settings(config: AppConfig, settings: RunSettings) -> None:
    mode = config.search.mode(settings.search_mode)
    try:
        SearchMode(
            candidate_max=settings.candidate_max or mode.candidate_max,
            deep_review_min=settings.deep_review_min or mode.deep_review_min,
            deep_review_max=settings.deep_review_max or mode.deep_review_max,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc))


def _run_view(database: Database, harness: WorkflowHarness, run_id: str) -> dict[str, Any]:
    run = database.get_run(run_id)
    progress = harness.progress(run_id)
    model_snapshot = run["config_snapshot"].get("model", {})
    return {
        "run_id": run_id,
        "case_id": run["case_id"],
        "parent_run_id": run["parent_run_id"],
        "status": run["status"],
        "input_text": run["input_text"],
        "input_hash": run["input_hash"],
        "evaluation_date": run["evaluation_date"],
        "date_basis": run["date_basis"],
        "analysis_scope": run["analysis_scope"],
        "model": run["model"],
        "base_url": model_snapshot.get("base_url"),
        "settings": run["settings_json"],
        "limitations": run["limitation_json"],
        "created_at": run["created_at"],
        "started_at": run["started_at"],
        "completed_at": run["completed_at"],
        "error_code": run["error_code"],
        "error_message": run["error_message"],
        "progress": progress,
    }


def _sse(value: dict[str, Any]) -> str:
    return f"data: {json.dumps(value, ensure_ascii=False)}\n\n"


def _safe_json(value: str | None, default: Any) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default
