from __future__ import annotations

from .agent_schemas import register_agent_output_model
from .followup import FollowupTurn
from .followup_answer import FollowupAnswer
from .followup_handler import FollowupSourceData
from .followup_plan import FollowupPlan
from .context import AssembledModelContext
from .model_client import StructuredModelClient


FOLLOWUP_PLANNER_PROMPT = """
你是专利评审追问规划器。只能依据当前问题、冻结文献范围、IDEA 特征和已完成历史轮次制定检索计划。
不得选择范围外公开号或不存在的 Feature ID，不得宣称已经找到证据，不得输出法律结论。查询改写应保留原问题中的
技术含义，可补充中英文同义表达，但不能加入新的产品事实。只返回符合 Schema 的 JSON。
"""

FOLLOWUP_ANSWER_PROMPT = """
你是专利评审追问回答器。只能把本轮 Context 中标记为 C1..Cn 的 Patent Evidence 作为专利事实依据。
Application context 和历史回答不是 Citation 证据。明确区分技术重合、差异、工程规避候选和法律判断；不得声称
构成侵权、保证不侵权或权利有效。证据不足时明确标记，所有高重合判断必须引用本轮 C#。只返回符合 Schema 的 JSON。
"""


class StructuredFollowupModel:
    """Use the existing transient-BYOK structured client for follow-up calls."""

    def __init__(self, client: StructuredModelClient) -> None:
        self.client = client
        register_agent_output_model("patent-followup-planner", FollowupPlan)
        register_agent_output_model("patent-followup-answerer", FollowupAnswer)

    async def plan(
        self, *, turn: FollowupTurn, source: FollowupSourceData
    ) -> FollowupPlan:
        result = await self.client.complete(
            "patent-followup-planner",
            system_prompt=FOLLOWUP_PLANNER_PROMPT,
            input_payload={
                "question": turn.question_text,
                "mode": turn.mode.value,
                "frozen_publication_numbers": list(turn.scope.publication_numbers),
                "features": [
                    {
                        "feature_id": item.feature_id,
                        "feature_text": item.feature_text,
                        "required": item.required,
                    }
                    for item in source.features
                ],
                "recent_turns": [
                    {
                        "turn_id": item.turn_id,
                        "question": item.question_text,
                        "answer": item.answer,
                    }
                    for item in source.recent_turns
                ],
                "source_report_summary": source.report_summary,
                "source_report_limitations": list(source.report_limitations),
            },
        )
        if not isinstance(result.output, FollowupPlan):
            raise TypeError("follow-up planner returned the wrong validated model")
        return result.output

    async def answer(
        self,
        *,
        turn: FollowupTurn,
        plan: FollowupPlan,
        context: AssembledModelContext,
    ) -> FollowupAnswer:
        result = await self.client.complete(
            "patent-followup-answerer",
            system_prompt=context.messages[0].content + "\n\n" + FOLLOWUP_ANSWER_PROMPT,
            input_payload={
                "question": turn.question_text,
                "plan": plan.model_dump(mode="json"),
                "context_id": context.context_id,
                "assembled_context": context.messages[1].content,
            },
        )
        if not isinstance(result.output, FollowupAnswer):
            raise TypeError("follow-up answerer returned the wrong validated model")
        return result.output


__all__ = [
    "FOLLOWUP_ANSWER_PROMPT",
    "FOLLOWUP_PLANNER_PROMPT",
    "StructuredFollowupModel",
]
