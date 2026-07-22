from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr, field_validator

from idea.model_client import RuntimeModelConfig

from .database import LandscapeDatabase
from .runtime import LandscapeRuntime
from .schemas import LandscapeScope
from .store import LandscapeStoreError


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LandscapeRuntimeRequest(ApiModel):
    api_key: SecretStr
    base_url: HttpUrl
    model: str = Field(min_length=1, max_length=200)

    @field_validator("api_key")
    @classmethod
    def nonempty_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("api_key must not be blank")
        return value

    @field_validator("model")
    @classmethod
    def clean_model(cls, value: str) -> str:
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

class CreateLandscapeRunRequest(LandscapeRuntimeRequest):
    scope: LandscapeScope


class DeleteLandscapeRunRequest(ApiModel):
    operator_label: str | None = Field(default=None, max_length=100)


def create_landscape_router(runtime: LandscapeRuntime) -> APIRouter:
    router = APIRouter(prefix="/api/landscape", tags=["专利态势分析"])
    database = runtime.database
    store = runtime.store
    tasks = runtime.tasks

    @router.post("/runs")
    async def create_run(request: CreateLandscapeRunRequest):
        try:
            run = database.create_run(
                scope=request.scope,
                model=request.model,
                workflow_version="landscape-workflow/1.0.0",
                prompt_version="landscape-prompts/1.0.0",
                config_snapshot={
                    "model": request.model,
                    "base_url": str(request.base_url).rstrip("/"),
                    "credential_source": "per_run_memory",
                    "serpapi_credential_source": "local_json",
                },
            )
            store.initialize_run(
                run["run_id"],
                scope=request.scope,
                model=request.model,
                workflow_version=run["workflow_version"],
                prompt_version=run["prompt_version"],
            )
            tasks.start(run["run_id"], request.runtime_config())
            return _run_view(runtime, run["run_id"])
        except (ValueError, LandscapeStoreError) as exc:
            raise HTTPException(422, str(exc))

    @router.get("/runs")
    async def list_runs(limit: int = 100):
        try:
            return {"runs": [_run_view(runtime, item["run_id"]) for item in database.list_runs(limit)]}
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    @router.get("/runs/{run_id}")
    async def get_run(run_id: str):
        try:
            return _run_view(runtime, run_id)
        except KeyError:
            raise HTTPException(404, "landscape run not found")

    @router.get("/runs/{run_id}/debug")
    async def get_run_debug(run_id: str):
        try:
            return database.debug_snapshot(run_id)
        except KeyError:
            raise HTTPException(404, "landscape run not found")

    @router.get("/runs/{run_id}/events")
    async def run_events(run_id: str):
        try:
            database.get_run(run_id)
        except KeyError:
            raise HTTPException(404, "landscape run not found")

        async def events():
            previous = None
            while True:
                try:
                    view = _run_view(runtime, run_id)
                except KeyError:
                    yield _sse({"type": "deleted", "run_id": run_id})
                    return
                encoded = json.dumps(view, ensure_ascii=False, sort_keys=True)
                if encoded != previous:
                    yield _sse({"type": "progress", "data": view})
                    previous = encoded
                if view["status"] in {"COMPLETED", "COMPLETED_WITH_LIMITATIONS", "FAILED", "CANCELLED"}:
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
            cancelled = await tasks.cancel(run_id)
        except KeyError:
            raise HTTPException(404, "landscape run not found")
        return {"run_id": run_id, "cancelled": cancelled, "status": database.get_run(run_id)["status"]}

    @router.post("/runs/{run_id}/rerun")
    async def rerun(run_id: str, request: LandscapeRuntimeRequest):
        try:
            source = database.get_run(run_id)
            scope = LandscapeScope.model_validate(source["scope_json"])
            new_run = database.create_run(
                scope=scope,
                model=request.model,
                workflow_version=source["workflow_version"],
                prompt_version=source["prompt_version"],
                config_snapshot={
                    "model": request.model,
                    "base_url": str(request.base_url).rstrip("/"),
                    "credential_source": "per_run_memory",
                    "serpapi_credential_source": "local_json",
                },
                parent_run_id=run_id,
            )
            store.initialize_run(
                new_run["run_id"], scope=scope, model=request.model,
                workflow_version=new_run["workflow_version"], prompt_version=new_run["prompt_version"],
            )
            tasks.start(new_run["run_id"], request.runtime_config())
            return _run_view(runtime, new_run["run_id"])
        except KeyError:
            raise HTTPException(404, "landscape run not found")
        except (ValueError, LandscapeStoreError) as exc:
            raise HTTPException(422, str(exc))

    @router.get("/runs/{run_id}/report")
    async def get_report(run_id: str):
        try:
            database.get_run(run_id)
            paths = store.paths(run_id)
            store.verify(run_id)
            return json.loads(paths.report_json.read_text(encoding="utf-8"))
        except KeyError:
            raise HTTPException(404, "landscape run not found")
        except (LandscapeStoreError, OSError, json.JSONDecodeError):
            raise HTTPException(404, "report not available")

    @router.get("/runs/{run_id}/report.md")
    async def download_markdown(run_id: str):
        try:
            database.get_run(run_id)
            paths = store.paths(run_id)
            store.verify(run_id)
        except KeyError:
            raise HTTPException(404, "landscape run not found")
        except LandscapeStoreError:
            raise HTTPException(404, "report not available")
        return FileResponse(paths.report_md, filename=f"landscape-{run_id}.md")

    @router.get("/runs/{run_id}/patents.csv")
    async def download_csv(run_id: str):
        try:
            database.get_run(run_id)
            paths = store.paths(run_id)
            store.verify(run_id)
        except KeyError:
            raise HTTPException(404, "landscape run not found")
        except LandscapeStoreError:
            raise HTTPException(404, "report not available")
        return FileResponse(paths.patents_csv, filename=f"landscape-{run_id}.csv", media_type="text/csv")

    @router.delete("/runs/{run_id}")
    async def delete_run(run_id: str, request: DeleteLandscapeRunRequest | None = None):
        try:
            await tasks.cancel(run_id)
            store.delete_run(run_id)
            database.delete_run(run_id)
        except KeyError:
            raise HTTPException(404, "landscape run not found")
        return {"deleted": run_id}

    return router


def _run_view(runtime: LandscapeRuntime, run_id: str) -> dict:
    run = runtime.database.get_run(run_id)
    return {
        "run_id": run_id,
        "status": run["status"],
        "mode": run["mode"],
        "scope": run["scope_json"],
        "model": run["model"],
        "publication_start": run["publication_start"],
        "publication_end": run["publication_end"],
        "limitations": run["limitation_json"],
        "created_at": run["created_at"],
        "started_at": run["started_at"],
        "completed_at": run["completed_at"],
        "error_code": run["error_code"],
        "error_message": run["error_message"],
        "progress": runtime.harness.progress(run_id),
    }


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
