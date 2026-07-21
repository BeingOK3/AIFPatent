from __future__ import annotations

import unittest

from pydantic import ValidationError

from idea.followup import FollowupError, FollowupMode, TurnStatus
from idea.followup_plan import FollowupPlan, FollowupPlanVerifier
from backend.tests.test_followup_context import turn


def plan(**updates) -> FollowupPlan:
    value = {
        "mode": "EVIDENCE_QA",
        "question_type": "CLAIM_OVERLAP",
        "selected_publication_numbers": ["CN123A"],
        "query_rewrites": ["缓存 热度 淘汰", "cache heat eviction"],
        "preferred_sections": ["claims", "description"],
        "required_features": ["F1"],
        "requires_new_research": False,
        "requires_legal_review": False,
        "rationale": "比较 F1 与独立权利要求。",
    }
    value.update(updates)
    return FollowupPlan.model_validate(value)


class FollowupPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.turn = turn("current", status=TurnStatus.RUNNING, created_at=100)

    def verify(self, value: FollowupPlan) -> FollowupPlan:
        return FollowupPlanVerifier().verify(
            value, turn=self.turn, allowed_feature_ids=("F1", "F2")
        )

    def test_valid_plan_normalizes_publication_and_maps_question_type(self) -> None:
        value = self.verify(plan(selected_publication_numbers=["CN-123-A"]))
        self.assertEqual(value.selected_publication_numbers, ["CN123A"])
        self.assertEqual(value.question_type.as_retrieval_type().value, "CLAIM_OVERLAP")

    def test_unknown_scope_feature_and_mode_fail_closed(self) -> None:
        with self.assertRaisesRegex(FollowupError, "outside the Turn scope"):
            self.verify(plan(selected_publication_numbers=["US999B2"]))
        with self.assertRaisesRegex(FollowupError, "unknown IDEA Features"):
            self.verify(plan(required_features=["F9"]))
        with self.assertRaisesRegex(FollowupError, "mode does not match"):
            self.verify(plan(mode="DESIGN_AROUND", question_type="DESIGN_AROUND"))

    def test_schema_rejects_duplicate_queries_and_invalid_sections(self) -> None:
        with self.assertRaises(ValidationError):
            plan(query_rewrites=["cache  eviction", "cache eviction"])
        with self.assertRaises(ValidationError):
            plan(preferred_sections=["claims", "bibliography"])

    def test_new_research_mode_requires_explicit_flag(self) -> None:
        research_turn = self.turn.__class__(
            **{**self.turn.__dict__, "mode": FollowupMode.NEW_RESEARCH}
        )
        with self.assertRaisesRegex(FollowupError, "must set requires_new_research"):
            FollowupPlanVerifier().verify(
                plan(mode="NEW_RESEARCH"),
                turn=research_turn,
                allowed_feature_ids=("F1",),
            )


if __name__ == "__main__":
    unittest.main()
