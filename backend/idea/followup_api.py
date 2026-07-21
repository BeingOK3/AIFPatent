from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import Field

from .api import ApiModel, RuntimeModelRequest
from .followup import (
    FollowupCitation,
    FollowupError,
    FollowupMode,
    FollowupScopeDocument,
    FollowupThread,
    FollowupTurn,
    TERMINAL_TURN_STATUSES,
    TurnStatus,
)
from .followup_workflow import FollowupWorkflow
from .model_client import RuntimeModelConfig, runtime_model_config
from .postgres_followup import PostgreSQLFollowupRepository


class CreateFollowupThreadRequest(ApiModel):
    title: str = Field(min_length=1, max_length=200)
    default_mode: FollowupMode = FollowupMode.EVIDENCE_QA
    publication_numbers: list[str] = Field(default_factory=list, max_length=100)


class CreateFollowupTurnRequest(RuntimeModelRequest):
    question: str = Field(min_length=2, max_length=20_000)
    mode: FollowupMode | None = None
    publication_numbers: list[str] = Field(default_factory=list, max_length=100)
    parent_turn_id: str | None = Field(default=None, max_length=100)


class FollowupTaskManager:
    """Own transient per-Turn credentials and cancellable workflow tasks."""

    def __init__(
        self,
        repository: PostgreSQLFollowupRepository,
        workflow: FollowupWorkflow,
    ) -> None:
        self.repository = repository
        self.workflow = workflow
        self.tasks: dict[str, asyncio.Task] = {}
        self._runtime_configs: dict[str, RuntimeModelConfig] = {}

    def start(self, turn_id: str, *, runtime_config: RuntimeModelConfig) -> bool:
        existing = self.tasks.get(turn_id)
        if existing is not None and not existing.done():
            return False
        self._runtime_configs[turn_id] = runtime_config
        self.tasks[turn_id] = asyncio.create_task(
            self._run(turn_id), name=f"followup-turn:{turn_id}"
        )
        return True

    async def _run(self, turn_id: str) -> None:
        try:
            config = self._runtime_configs.get(turn_id)
            if config is None:
                await self.repository.fail_turn(
                    turn_id,
                    error_code="RUNTIME_API_KEY_REQUIRED",
                    error_message="当前追问缺少临时 API Token，请重新提交 Turn。",
                )
                return
            with runtime_model_config(config):
                await self.workflow.execute(turn_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            current = await self.repository.get_turn(turn_id)
            if current is not None and current.status in {
                TurnStatus.QUEUED,
                TurnStatus.RUNNING,
            }:
                await self.repository.fail_turn(
                    turn_id,
                    error_code=type(exc).__name__,
                    error_message=str(exc) or "follow-up task failed",
                )
        finally:
            if self.tasks.get(turn_id) is asyncio.current_task():
                self.tasks.pop(turn_id, None)
            self._runtime_configs.pop(turn_id, None)

    async def cancel(self, turn_id: str) -> bool:
        turn = await self.repository.get_turn(turn_id)
        if turn is None:
            raise KeyError(turn_id)
        if turn.status in TERMINAL_TURN_STATUSES:
            return False
        task = self.tasks.get(turn_id)
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        current = await self.repository.get_turn(turn_id)
        if current is not None and current.status not in TERMINAL_TURN_STATUSES:
            await self.repository.cancel_turn(turn_id)
        self._runtime_configs.pop(turn_id, None)
        return True

    async def resume_incomplete(self) -> tuple[str, ...]:
        return await self.repository.fail_incomplete_after_restart()

    async def aclose(self) -> None:
        for task in tuple(self.tasks.values()):
            if not task.done():
                task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        self.tasks.clear()
        self._runtime_configs.clear()
        await self.workflow.aclose()


def create_followup_router(
    repository: PostgreSQLFollowupRepository,
    manager: FollowupTaskManager,
) -> APIRouter:
    router = APIRouter(prefix="/api/idea", tags=["IDEA Follow-up"])

    @router.get("/runs/{run_id}/followups/documents")
    async def eligible_documents(run_id: str):
        try:
            documents = await repository.eligible_documents(run_id)
        except FollowupError as exc:
            raise _http_error(exc)
        return {"run_id": run_id, "documents": [_document_view(item) for item in documents]}

    @router.post("/runs/{run_id}/followups/threads")
    async def create_thread(run_id: str, request: CreateFollowupThreadRequest):
        try:
            thread = await repository.create_thread(
                run_id=run_id,
                title=request.title,
                default_mode=request.default_mode,
                publication_numbers=request.publication_numbers,
            )
        except FollowupError as exc:
            raise _http_error(exc)
        return _thread_view(thread)

    @router.get("/runs/{run_id}/followups/threads")
    async def list_threads(run_id: str):
        return {
            "run_id": run_id,
            "threads": [_thread_view(item) for item in await repository.list_threads(run_id)],
        }

    @router.get("/followups/threads/{thread_id}")
    async def get_thread(thread_id: str):
        thread = await repository.get_thread(thread_id)
        if thread is None:
            raise HTTPException(404, "follow-up Thread not found")
        turns = await repository.list_turns(thread_id)
        return {
            **_thread_view(thread),
            "turns": [await _turn_view(repository, item) for item in turns],
        }

    @router.post("/followups/threads/{thread_id}/archive")
    async def archive_thread(thread_id: str):
        try:
            return _thread_view(await repository.archive_thread(thread_id))
        except FollowupError as exc:
            raise _http_error(exc)

    @router.post("/followups/threads/{thread_id}/turns")
    async def create_turn(thread_id: str, request: CreateFollowupTurnRequest):
        try:
            turn = await repository.create_turn(
                thread_id=thread_id,
                question=request.question,
                model=request.model,
                prompt_version="followup-answer-v1",
                retriever_version="hybrid-rrf-v1",
                mode=request.mode,
                publication_numbers=request.publication_numbers,
                parent_turn_id=request.parent_turn_id,
            )
        except FollowupError as exc:
            raise _http_error(exc)
        if not manager.start(turn.turn_id, runtime_config=request.runtime_config()):
            raise HTTPException(409, "follow-up Turn is already running")
        return await _turn_view(repository, turn)

    @router.get("/followups/turns/{turn_id}")
    async def get_turn(turn_id: str):
        turn = await repository.get_turn(turn_id)
        if turn is None:
            raise HTTPException(404, "follow-up Turn not found")
        return await _turn_view(repository, turn)

    @router.get("/followups/turns/{turn_id}/events")
    async def turn_events(turn_id: str):
        if await repository.get_turn(turn_id) is None:
            raise HTTPException(404, "follow-up Turn not found")

        async def events():
            previous = None
            while True:
                turn = await repository.get_turn(turn_id)
                if turn is None:
                    yield _sse({"type": "deleted", "turn_id": turn_id})
                    return
                view = await _turn_view(repository, turn)
                encoded = json.dumps(view, ensure_ascii=False, sort_keys=True)
                if encoded != previous:
                    yield _sse({"type": "progress", "data": view})
                    previous = encoded
                if turn.status in TERMINAL_TURN_STATUSES:
                    yield _sse({"type": "terminal", "data": view})
                    return
                await asyncio.sleep(0.5)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.post("/followups/turns/{turn_id}/cancel")
    async def cancel_turn(turn_id: str):
        try:
            cancelled = await manager.cancel(turn_id)
        except KeyError:
            raise HTTPException(404, "follow-up Turn not found")
        turn = await repository.get_turn(turn_id)
        return {"turn_id": turn_id, "cancelled": cancelled, "status": turn.status.value}

    @router.get("/followups/citations/{citation_id}")
    async def get_citation(citation_id: str):
        citation = await repository.get_citation(citation_id)
        if citation is None:
            raise HTTPException(404, "follow-up Citation not found")
        return _citation_view(citation)

    return router


def _document_view(value: FollowupScopeDocument) -> dict:
    return {
        "document_id": value.document_id,
        "version_id": value.version_id,
        "publication_number": value.publication_number,
        "content_sha256": value.content_sha256,
    }


def _thread_view(value: FollowupThread) -> dict:
    return {
        "thread_id": value.thread_id,
        "run_id": value.run_id,
        "title": value.title,
        "status": value.status.value,
        "default_mode": value.default_mode.value,
        "scope": value.scope.as_json(),
        "corpus_snapshot_hash": value.scope.corpus_snapshot_hash,
        "created_at": value.created_at,
        "updated_at": value.updated_at,
    }


async def _turn_view(
    repository: PostgreSQLFollowupRepository, value: FollowupTurn
) -> dict:
    citations = (
        await repository.list_citations(value.turn_id)
        if value.status in {TurnStatus.COMPLETED, TurnStatus.COMPLETED_WITH_LIMITATIONS}
        else ()
    )
    return {
        "turn_id": value.turn_id,
        "thread_id": value.thread_id,
        "parent_turn_id": value.parent_turn_id,
        "status": value.status.value,
        "mode": value.mode.value,
        "question": value.question_text,
        "scope": value.scope.as_json(),
        "plan": value.plan,
        "answer": value.answer,
        "model": value.model,
        "prompt_version": value.prompt_version,
        "retriever_version": value.retriever_version,
        "limitations": list(value.limitations),
        "error_code": value.error_code,
        "error_message": value.error_message,
        "citations": [_citation_view(item) for item in citations],
        "created_at": value.created_at,
        "started_at": value.started_at,
        "completed_at": value.completed_at,
    }


def _citation_view(value: FollowupCitation) -> dict:
    return {
        "citation_id": value.citation_id,
        "turn_id": value.turn_id,
        "chunk_id": value.chunk_id,
        "publication_number": value.publication_number,
        "section_type": value.section_type,
        "section_label": value.section_label,
        "quote_text": value.quote_text,
        "quote_hash": value.quote_hash,
        "start_offset": value.start_offset,
        "end_offset": value.end_offset,
        "answer_path": value.answer_path,
    }


def _http_error(error: FollowupError) -> HTTPException:
    message = str(error)
    status = 404 if "does not exist" in message or "disappeared" in message else 409
    return HTTPException(status, message)


def _sse(value: dict) -> str:
    return f"data: {json.dumps(value, ensure_ascii=False)}\n\n"


__all__ = [
    "CreateFollowupThreadRequest",
    "CreateFollowupTurnRequest",
    "FollowupTaskManager",
    "create_followup_router",
]
