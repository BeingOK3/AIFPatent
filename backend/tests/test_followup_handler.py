from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace
from types import SimpleNamespace

from idea.agent_schemas import IdeaFeature
from idea.context import ContextAssembler
from idea.followup import FollowupError, TurnStatus
from idea.followup_answer import FollowupAnswer
from idea.followup_context import FollowupContextBuilder
from idea.followup_handler import FollowupBusinessHandler, FollowupSourceData
from idea.followup_plan import FollowupPlan
from idea.followup_workflow import FOLLOWUP_WORKFLOW_STEPS, FollowupWorkflowStep
from backend.tests.test_followup_context import chunk, retrieval, turn


class MemoryRepository:
    def __init__(self) -> None:
        self.turn = turn("current", status=TurnStatus.RUNNING, created_at=100)
        self.thread = SimpleNamespace(thread_id="thread-1", run_id="run-1")
        self.plan = None
        self.selected = None
        self.citations = ()
        self.completed_answer = None
        self.completed_limitations = ()

    async def get_turn(self, turn_id):
        return self.turn if turn_id == self.turn.turn_id else None

    async def get_thread(self, thread_id):
        return self.thread if thread_id == self.thread.thread_id else None

    async def save_plan(self, turn_id, plan):
        self.plan = plan
        return self.turn

    async def record_retrieval(self, turn_id, result, *, selected_chunk_ids=None):
        self.selected = tuple(selected_chunk_ids or ())
        return len(result.hits)

    async def record_citations(self, turn_id, drafts):
        self.citations = tuple(drafts)
        return self.citations

    async def complete_turn(self, turn_id, *, answer, limitations=()):
        self.completed_answer = answer
        self.completed_limitations = tuple(limitations)
        self.turn = replace(
            self.turn,
            status=(
                TurnStatus.COMPLETED_WITH_LIMITATIONS
                if limitations
                else TurnStatus.COMPLETED
            ),
            answer=answer,
            limitations=tuple(limitations),
            completed_at=101,
        )
        return self.turn


class DataSource:
    async def load(self, *, run_id, turn):
        return FollowupSourceData(
            features=(IdeaFeature(
                feature_id="F1",
                feature_text="根据热度淘汰缓存块",
                source_type="normalized",
            ),),
            report_summary="首次报告存在部分重合。",
            report_limitations=("仅评价技术披露",),
        )


class Model:
    def __init__(self) -> None:
        self.context_ids = []

    async def plan(self, *, turn, source):
        return FollowupPlan.model_validate({
            "mode": "EVIDENCE_QA",
            "question_type": "CLAIM_OVERLAP",
            "selected_publication_numbers": ["CN123A"],
            "query_rewrites": ["热度 缓存 淘汰"],
            "preferred_sections": ["claims"],
            "required_features": ["F1"],
            "requires_new_research": False,
            "requires_legal_review": False,
            "rationale": "比较技术特征。",
        })

    async def answer(self, *, turn, plan, context):
        self.context_ids.append(context.context_id)
        return FollowupAnswer.model_validate({
            "answer_type": "DIRECT",
            "direct_answer": "CN123A 的权利要求披露了按热度淘汰缓存块。",
            "citation_aliases": ["C1"],
            "overlap_items": [],
            "differences": [],
            "design_around_options": [],
            "legal_boundary": "这是技术披露比较，不是侵权或有效性法律结论。",
            "limitations": [],
            "needs_new_research": False,
        })


class Retriever:
    async def retrieve(self, *, turn, plan):
        return retrieval(chunk())


class ContextRepository:
    def __init__(self) -> None:
        self.contexts = []

    async def put_if_absent(self, context, *, agent_name):
        self.contexts.append((context, agent_name))
        return context.context_id


class FollowupBusinessHandlerTests(unittest.TestCase):
    def handler(self):
        repository = MemoryRepository()
        contexts = ContextRepository()
        model = Model()
        handler = FollowupBusinessHandler(
            repository=repository,
            data_source=DataSource(),
            model=model,
            retriever=Retriever(),
            context_builder=FollowupContextBuilder(ContextAssembler()),
            context_repository=contexts,
            system_prompt="仅依据本轮证据回答并使用 C#。",
            input_budget=10000,
            reserved_output_tokens=1000,
        )
        return handler, repository, contexts, model

    def test_all_business_nodes_persist_verified_answer_and_citations(self) -> None:
        async def scenario():
            handler, repository, contexts, model = self.handler()
            for step in FOLLOWUP_WORKFLOW_STEPS:
                await handler.execute("current", step, 1)
            return handler, repository, contexts, model

        handler, repository, contexts, model = asyncio.run(scenario())
        self.assertEqual(repository.plan["required_features"], ["F1"])
        self.assertEqual(repository.selected, (chunk().chunk_id,))
        self.assertEqual(len(contexts.contexts), 1)
        self.assertEqual(contexts.contexts[0][1], "patent-followup-answerer")
        self.assertEqual(model.context_ids, [contexts.contexts[0][0].context_id])
        self.assertEqual(len(repository.citations), 1)
        self.assertEqual(repository.citations[0].answer_path, "direct_answer")
        self.assertEqual(repository.turn.status, TurnStatus.COMPLETED_WITH_LIMITATIONS)
        self.assertIn("LEXICAL_ONLY", repository.completed_limitations)
        self.assertEqual(repository.completed_answer["answer_type"], "DIRECT")
        handler.discard("current")
        self.assertNotIn("current", handler._memory)

    def test_steps_fail_when_ephemeral_prerequisites_are_missing(self) -> None:
        handler, _, _, _ = self.handler()
        with self.assertRaisesRegex(FollowupError, "unavailable after restart"):
            asyncio.run(
                handler.execute(
                    "current", FollowupWorkflowStep.GENERATE_FOLLOWUP_ANSWER, 1
                )
            )

    def test_prepare_rejects_non_running_turn(self) -> None:
        handler, repository, _, _ = self.handler()
        repository.turn = replace(repository.turn, status=TurnStatus.QUEUED)
        with self.assertRaisesRegex(FollowupError, "RUNNING Turn"):
            asyncio.run(
                handler.execute(
                    "current", FollowupWorkflowStep.PREPARE_FOLLOWUP_SCOPE, 1
                )
            )


if __name__ == "__main__":
    unittest.main()
