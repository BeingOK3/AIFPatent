from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr, field_validator

from idea.model_client import RuntimeModelConfig, runtime_model_config

from .database import LandscapeDatabase
from .runtime import LandscapeRuntime
from .schemas import LandscapeScope
from .scope import ScopeDraft, ScopeDraftStatus
from .scope_repository import ScopePersistenceError, ScopeRevisionConflict
from .scope_service import ScopePreparationError
from .run_repository import LandscapeRunPersistenceError
from .query_planning import build_query_plan
from .query_repository import QueryPlanPersistenceError
from .scale_repository import ScaleGatePersistenceError
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
        # HttpUrl accepts a second URL-looking sequence in the path, such as
        # ``https://host/v3https://host/v3``. That produces opaque 404 errors
        # from OpenAI-compatible gateways, so reject it before a Run exists.
        if str(value).count("://") != 1:
            raise ValueError("base_url must contain exactly one URL")
        return value

    def runtime_config(self) -> RuntimeModelConfig:
        return RuntimeModelConfig(
            base_url=str(self.base_url).rstrip("/"),
            api_key=self.api_key.get_secret_value(),
            model=self.model,
        )

class CreateLandscapeRunRequest(ApiModel):
    scope_revision_id: str = Field(pattern=r"^SCR-[0-9a-f]{16}$")


class DeleteLandscapeRunRequest(ApiModel):
    operator_label: str | None = Field(default=None, max_length=100)


class CreateScopeDraftRequest(ApiModel):
    company_names: tuple[str, ...] = Field(default=(), max_length=50)
    technology_input: str | None = Field(default=None, max_length=500)
    publication_start: date
    publication_end: date


class ExpandScopeDraftRequest(LandscapeRuntimeRequest):
    expected_revision: int = Field(ge=1)


class PatchScopeDraftRequest(ApiModel):
    expected_revision: int = Field(ge=1)
    draft: ScopeDraft


class ConfirmScopeDraftRequest(ApiModel):
    expected_revision: int = Field(ge=1)


class ScaleDecisionRequest(ApiModel):
    approve: bool


def create_landscape_router(runtime: LandscapeRuntime) -> APIRouter:
    router = APIRouter(prefix="/api/landscape", tags=["专利态势分析"])
    database = runtime.database
    store = runtime.store
    tasks = runtime.tasks

    @router.post("/scope-drafts", status_code=201)
    async def create_scope_draft(request: CreateScopeDraftRequest):
        try:
            return runtime.scope_service.create_draft(
                company_names=request.company_names,
                technology_input=request.technology_input,
                publication_start=request.publication_start,
                publication_end=request.publication_end,
            )
        except (ValueError, ScopePersistenceError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/scope-drafts/{draft_id}")
    async def get_scope_draft(draft_id: str):
        try:
            return runtime.scope_repository.get(draft_id)
        except KeyError as exc:
            raise HTTPException(404, "scope draft not found") from exc
        except ScopePersistenceError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/scope-drafts/{draft_id}/expand")
    async def expand_scope_draft(
        draft_id: str,
        request: ExpandScopeDraftRequest,
    ):
        try:
            with runtime_model_config(request.runtime_config()):
                return await runtime.scope_service.expand_draft(
                    draft_id,
                    expected_revision=request.expected_revision,
                )
        except KeyError as exc:
            raise HTTPException(404, "scope draft not found") from exc
        except (ScopeRevisionConflict, ScopePreparationError) as exc:
            raise HTTPException(409, str(exc)) from exc
        except (ValueError, ScopePersistenceError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.patch("/scope-drafts/{draft_id}")
    async def patch_scope_draft(
        draft_id: str,
        request: PatchScopeDraftRequest,
    ):
        if request.draft.draft_id != draft_id:
            raise HTTPException(422, "draft ID in path and body must match")
        if request.draft.revision != request.expected_revision + 1:
            raise HTTPException(422, "draft revision must equal expected_revision + 1")
        try:
            current = runtime.scope_repository.get(draft_id)
            if current.status != ScopeDraftStatus.AWAITING_CONFIRMATION:
                raise ScopePreparationError(
                    "only an AWAITING_CONFIRMATION scope can be edited"
                )
            # Expansion limitations are server-owned audit evidence. The user
            # can edit the proposed scope, but cannot erase warnings by sending
            # a replacement draft body.
            reviewable = request.draft.model_copy(
                update={
                    "status": ScopeDraftStatus.AWAITING_CONFIRMATION,
                    "limitations": current.limitations,
                }
            )
            return runtime.scope_repository.update(
                reviewable,
                expected_revision=request.expected_revision,
            )
        except KeyError as exc:
            raise HTTPException(404, "scope draft not found") from exc
        except ScopeRevisionConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except (ValueError, ScopePersistenceError, ScopePreparationError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/scope-drafts/{draft_id}/confirm")
    async def confirm_scope_draft(
        draft_id: str,
        request: ConfirmScopeDraftRequest,
    ):
        try:
            return runtime.scope_repository.confirm(
                draft_id,
                expected_revision=request.expected_revision,
            )
        except KeyError as exc:
            raise HTTPException(404, "scope draft not found") from exc
        except ScopeRevisionConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except (ValueError, ScopePersistenceError) as exc:
            raise HTTPException(422, str(exc)) from exc

    # @router.post("/runs") is the v4 formal Run endpoint (201 on success).
    @router.post("/runs", status_code=201)
    async def create_run(request: CreateLandscapeRunRequest):
        if hasattr(runtime, "v4_workflow") and (
            runtime.v4_workflow is None or runtime.v4_tasks is None
        ):
            raise HTTPException(503, "Landscape v4 requires a paged patent provider")
        try:
            run = runtime.run_repository.create(
                scope_revision_id=request.scope_revision_id,
                taxonomy_version=runtime.taxonomy.taxonomy_version,
            )
            scope = runtime.scope_repository.get_confirmed(request.scope_revision_id)
            plan = build_query_plan(scope)
            runtime.query_repository.put(run.run_id, plan)
            runtime.stage_repository.ensure(run.run_id)
            if getattr(runtime, "v4_tasks", None) is not None:
                runtime.v4_tasks.start(run.run_id)
            return runtime.run_repository.get(run.run_id)
        except (KeyError, ValueError, ScopePersistenceError,
                LandscapeRunPersistenceError, QueryPlanPersistenceError) as exc:
            raise HTTPException(422, str(exc))

    @router.get("/runs")
    async def list_runs(limit: int = 100):
        try:
            return {
                "runs": [
                    _run_view(runtime, run.run_id)
                    for run in runtime.run_repository.list(limit)
                ]
            }
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    @router.post("/runs/{run_id}/scale-decision")
    async def decide_scale(run_id: str, request: ScaleDecisionRequest):
        try:
            decision = runtime.scale_repository.decide(
                run_id,
                approve=request.approve,
            )
            if request.approve and getattr(runtime, "v4_tasks", None) is not None:
                runtime.v4_tasks.start(run_id)
            return decision
        except KeyError as exc:
            raise HTTPException(404, "scale gate not found") from exc
        except ScaleGatePersistenceError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/runs/{run_id}/credentials")
    async def provide_credentials(run_id: str, request: LandscapeRuntimeRequest):
        try:
            runtime.run_repository.get(run_id)
        except KeyError as exc:
            raise HTTPException(404, "landscape run not found") from exc
        runtime.credential_vault.put(run_id, request.runtime_config())
        if getattr(runtime, "v4_tasks", None) is not None:
            runtime.v4_tasks.start(run_id)
        return {"run_id": run_id, "credentials_available": True}

    @router.get("/runs/{run_id}")
    async def get_run(run_id: str):
        try:
            return _run_view(runtime, run_id)
        except KeyError:
            raise HTTPException(404, "landscape run not found")

    @router.get("/runs/{run_id}/debug")
    async def get_run_debug(run_id: str):
        try:
            run = runtime.run_repository.get(run_id)
            plan = runtime.query_repository.get(run_id)
            try:
                gate = runtime.scale_repository.get(run_id)
            except KeyError:
                gate = None
            return {
                "run_id": run.run_id,
                "scope_revision_id": run.scope_revision_id,
                "taxonomy_version": run.taxonomy_version,
                "query_plan": plan,
                "scale_gate": gate,
                "stages": runtime.stage_repository.list(run_id),
                "limitations": runtime.stage_repository.limitations(run_id),
            }
        except KeyError:
            raise HTTPException(404, "landscape run not found")

    @router.get("/runs/{run_id}/events")
    async def run_events(run_id: str):
        try:
            runtime.run_repository.get(run_id)
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
            if getattr(runtime, "v4_tasks", None) is not None:
                await runtime.v4_tasks.cancel(run_id)
            previous = runtime.run_repository.get(run_id)
            run = runtime.run_repository.cancel(run_id)
            runtime.credential_vault.revoke(run_id)
        except KeyError:
            raise HTTPException(404, "landscape run not found")
        return {
            "run_id": run_id,
            "cancelled": run.status != previous.status,
            "status": run.status,
        }

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

    @router.post("/runs/{run_id}/deep-analyze")
    async def deep_analyze_selected(
        run_id: str, request: LandscapeRuntimeRequest
    ):
        """Run optional deep analysis only for persisted selected patents."""
        try:
            run = database.get_run(run_id)
            if run["status"] not in {"COMPLETED", "COMPLETED_WITH_LIMITATIONS"}:
                raise HTTPException(
                    409,
                    "deep analysis requires a completed landscape run",
                )
            with runtime_model_config(request.runtime_config()):
                result = await runtime.execution.analyze_selected_patents(run_id)
                report = await runtime.execution.build_report(
                    run_id, deep_analysis=result, allow_report_revision=True
                )
            return {
                "run_id": run_id,
                "deep_analysis": result,
                "report": report["report"],
            }
        except KeyError:
            raise HTTPException(404, "landscape run not found")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(422, str(exc)[:2000])

    @router.get("/runs/{run_id}/report")
    async def get_report(run_id: str):
        try:
            report, _markdown = runtime.report_v4_repository.get(run_id)
            return report
        except KeyError:
            raise HTTPException(404, "landscape run not found")

    @router.get("/runs/{run_id}/report.md")
    async def download_markdown(run_id: str):
        try:
            _report, markdown = runtime.report_v4_repository.get(run_id)
        except KeyError:
            raise HTTPException(404, "landscape run not found")
        return Response(
            markdown,
            media_type="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="landscape-{run_id}.md"'
            },
        )

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
    run = runtime.run_repository.get(run_id)
    scope = runtime.scope_repository.get_confirmed(run.scope_revision_id)
    try:
        stages = runtime.stage_repository.list(run_id)
    except KeyError:
        stages = ()
    try:
        scale_gate = runtime.scale_repository.get(run_id)
    except KeyError:
        scale_gate = None
    return {
        "run_id": run_id,
        "status": run.status,
        "mode": run.mode,
        "scope_revision_id": run.scope_revision_id,
        "taxonomy_version": run.taxonomy_version,
        "scope": scope,
        "publication_start": run.publication_start,
        "publication_end": run.publication_end,
        "limitations": runtime.stage_repository.limitations(run_id),
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "error_code": run.error_code,
        "error_message": run.error_message,
        "scale_gate": scale_gate,
        "credentials_available": runtime.credential_vault.has_credentials(run_id),
        "progress": {
            "completed_stages": sum(
                stage.status in {"SUCCEEDED", "SUCCEEDED_WITH_LIMITATIONS"}
                for stage in stages
            ),
            "total_stages": len(stages),
            "steps": stages,
        },
    }


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
