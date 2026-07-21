from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from idea.agent_schemas import IdeaFeature, agent_json_schema
from idea.context import ContextAssembler
from idea.followup import TurnStatus
from idea.followup_context import FollowupContextBuilder
from idea.followup_handler import FollowupSourceData
from idea.followup_model import StructuredFollowupModel
from idea.followup_plan import FollowupPlan
from backend.tests.test_followup_context import chunk, retrieval, turn


class Client:
    def __init__(self, outputs) -> None:
        self.outputs = list(outputs)
        self.calls = []

    async def complete(self, agent_name, *, system_prompt, input_payload):
        self.calls.append((agent_name, system_prompt, input_payload))
        return SimpleNamespace(output=self.outputs.pop(0))


class StructuredFollowupModelTests(unittest.TestCase):
    def test_plan_and_answer_use_registered_strict_schemas_without_credentials_in_payload(self):
        current = turn("current", status=TurnStatus.RUNNING, created_at=100)
        feature = IdeaFeature(
            feature_id="F1", feature_text="根据热度淘汰缓存块", source_type="normalized"
        )
        plan = FollowupPlan.model_validate({
            "mode": "EVIDENCE_QA",
            "question_type": "CLAIM_OVERLAP",
            "selected_publication_numbers": ["CN123A"],
            "query_rewrites": ["热度 淘汰"],
            "preferred_sections": ["claims"],
            "required_features": ["F1"],
            "requires_new_research": False,
            "requires_legal_review": False,
            "rationale": "比较本轮证据。",
        })
        from idea.followup_answer import FollowupAnswer
        answer = FollowupAnswer.model_validate({
            "answer_type": "DIRECT",
            "direct_answer": "该权利要求披露了热度淘汰机制。",
            "citation_aliases": ["C1"],
            "overlap_items": [],
            "differences": [],
            "design_around_options": [],
            "legal_boundary": "仅作技术比较，不作法律结论。",
            "limitations": [],
            "needs_new_research": False,
        })
        client = Client((plan, answer))
        model = StructuredFollowupModel(client)
        source = FollowupSourceData(features=(feature,))
        context = FollowupContextBuilder(ContextAssembler()).build(
            run_id="run-1", turn=current, features=(feature,), recent_turns=(),
            retrieval=retrieval(chunk()), system_prompt="仅依据证据回答。",
            input_budget=10000, reserved_output_tokens=1000,
        ).context

        async def scenario():
            planned = await model.plan(turn=current, source=source)
            answered = await model.answer(turn=current, plan=planned, context=context)
            return planned, answered

        planned, answered = asyncio.run(scenario())
        self.assertIs(planned, plan)
        self.assertIs(answered, answer)
        self.assertIn("properties", agent_json_schema("patent-followup-planner"))
        self.assertIn("properties", agent_json_schema("patent-followup-answerer"))
        encoded_calls = str(client.calls).lower()
        self.assertNotIn("api_key", encoded_calls)
        self.assertNotIn("authorization", encoded_calls)
        self.assertEqual(client.calls[1][2]["context_id"], context.context_id)


if __name__ == "__main__":
    unittest.main()
