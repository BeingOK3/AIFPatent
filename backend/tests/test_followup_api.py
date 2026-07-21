from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from idea.config import load_config
from idea.followup import (
    FollowupCitation,
    FollowupMode,
    FollowupThread,
    ThreadStatus,
    TurnStatus,
)
from idea.followup_api import FollowupTaskManager, create_followup_router
from idea.model_client import StructuredModelClient
from backend.tests.test_followup_context import scope, turn


class Repository:
    def __init__(self) -> None:
        self.thread = FollowupThread(
            thread_id="thread-1", run_id="run-1", title="追问",
            status=ThreadStatus.ACTIVE, default_mode=FollowupMode.EVIDENCE_QA,
            scope=scope(), created_at=1, updated_at=1,
        )
        self.turn = turn("turn-1", status=TurnStatus.QUEUED, created_at=2)
        self.citation = FollowupCitation(
            citation_id="FC-1", turn_id="turn-1", chunk_id="chunk-1",
            publication_number="CN123A", section_type="claims",
            section_label="claim-1", quote_text="权利要求证据",
            quote_hash="a" * 64, start_offset=0, end_offset=6,
            answer_path="direct_answer",
        )

    async def eligible_documents(self, run_id):
        return scope().documents

    async def create_thread(self, **kwargs):
        return self.thread

    async def list_threads(self, run_id=None):
        return (self.thread,)

    async def get_thread(self, thread_id):
        return self.thread if thread_id == self.thread.thread_id else None

    async def list_turns(self, thread_id):
        return (self.turn,)

    async def archive_thread(self, thread_id):
        self.thread = replace(self.thread, status=ThreadStatus.ARCHIVED)
        return self.thread

    async def create_turn(self, **kwargs):
        return self.turn

    async def get_turn(self, turn_id):
        return self.turn if turn_id == self.turn.turn_id else None

    async def list_citations(self, turn_id):
        return (self.citation,)

    async def get_citation(self, citation_id):
        return self.citation if citation_id == self.citation.citation_id else None

    async def cancel_turn(self, turn_id):
        self.turn = replace(self.turn, status=TurnStatus.CANCELLED, completed_at=3)
        return self.turn

    async def fail_turn(self, turn_id, *, error_code, error_message):
        self.turn = replace(
            self.turn, status=TurnStatus.FAILED, error_code=error_code,
            error_message=error_message, completed_at=3,
        )
        return self.turn

    async def fail_incomplete_after_restart(self):
        return ("turn-1",)


class Manager:
    def __init__(self, repository):
        self.repository = repository
        self.started = []

    def start(self, turn_id, *, runtime_config):
        self.started.append((turn_id, runtime_config))
        return True

    async def cancel(self, turn_id):
        await self.repository.cancel_turn(turn_id)
        return True


class FollowupRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = Repository()
        self.manager = Manager(self.repository)
        app = FastAPI()
        app.include_router(create_followup_router(self.repository, self.manager))
        self.client = TestClient(app)

    def test_thread_documents_and_turn_byok_are_exposed_without_secret_echo(self) -> None:
        documents = self.client.get("/api/idea/runs/run-1/followups/documents")
        self.assertEqual(documents.status_code, 200)
        self.assertEqual(documents.json()["documents"][0]["version_id"], "cv-1")

        created_thread = self.client.post(
            "/api/idea/runs/run-1/followups/threads",
            json={"title": "分析缓存特征", "publication_numbers": ["CN123A"]},
        )
        self.assertEqual(created_thread.status_code, 200)
        self.assertEqual(created_thread.json()["thread_id"], "thread-1")

        created_turn = self.client.post(
            "/api/idea/followups/threads/thread-1/turns",
            json={
                "question": "权利要求是否披露热度淘汰？",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-v4-flash",
                "api_key": "temporary-test-token",
            },
        )
        self.assertEqual(created_turn.status_code, 200)
        body = created_turn.text.lower()
        self.assertNotIn("temporary-test-token", body)
        self.assertNotIn("api_key", body)
        self.assertEqual(self.manager.started[0][0], "turn-1")
        self.assertEqual(
            self.manager.started[0][1].api_key, "temporary-test-token"
        )

    def test_terminal_turn_includes_expandable_citation_and_sse_terminal(self) -> None:
        self.repository.turn = replace(
            self.repository.turn,
            status=TurnStatus.COMPLETED,
            answer={"answer_type": "DIRECT", "direct_answer": "有披露"},
            completed_at=3,
        )
        response = self.client.get("/api/idea/followups/turns/turn-1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["citations"][0]["citation_id"], "FC-1")
        citation = self.client.get("/api/idea/followups/citations/FC-1")
        self.assertEqual(citation.json()["quote_text"], "权利要求证据")

        events = self.client.get("/api/idea/followups/turns/turn-1/events")
        self.assertEqual(events.status_code, 200)
        self.assertIn('"type": "terminal"', events.text)


class Workflow:
    def __init__(self, repository, *, block=False) -> None:
        self.repository = repository
        self.block = block
        self.seen = None
        self.closed = False

    async def execute(self, turn_id):
        client = StructuredModelClient(load_config().model)
        self.seen = (client.base_url(), client.model_name(), client.api_key())
        if self.block:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await self.repository.cancel_turn(turn_id)
                raise
        self.repository.turn = replace(
            self.repository.turn,
            status=TurnStatus.COMPLETED,
            answer={"answer_type": "DIRECT", "direct_answer": "完成"},
            completed_at=3,
        )

    async def aclose(self):
        self.closed = True


class FollowupTaskManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_credential_exists_only_during_task(self) -> None:
        repository = Repository()
        workflow = Workflow(repository)
        manager = FollowupTaskManager(repository, workflow)
        from idea.model_client import RuntimeModelConfig
        manager.start(
            "turn-1",
            runtime_config=RuntimeModelConfig(
                "https://api.deepseek.com", "task-secret", "deepseek-v4-flash"
            ),
        )
        await asyncio.gather(*tuple(manager.tasks.values()))
        self.assertEqual(
            workflow.seen,
            ("https://api.deepseek.com", "deepseek-v4-flash", "task-secret"),
        )
        self.assertEqual(manager.tasks, {})
        self.assertEqual(manager._runtime_configs, {})
        await manager.aclose()
        self.assertTrue(workflow.closed)

    async def test_cancel_and_restart_failure_do_not_retain_credentials(self) -> None:
        repository = Repository()
        workflow = Workflow(repository, block=True)
        manager = FollowupTaskManager(repository, workflow)
        from idea.model_client import RuntimeModelConfig
        manager.start(
            "turn-1",
            runtime_config=RuntimeModelConfig(
                "https://api.deepseek.com", "cancel-secret", "deepseek-v4-flash"
            ),
        )
        await asyncio.sleep(0)
        self.assertTrue(await manager.cancel("turn-1"))
        self.assertEqual(repository.turn.status, TurnStatus.CANCELLED)
        self.assertEqual(manager._runtime_configs, {})
        self.assertEqual(await manager.resume_incomplete(), ("turn-1",))
        await manager.aclose()


if __name__ == "__main__":
    unittest.main()
