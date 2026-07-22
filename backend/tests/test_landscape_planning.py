from __future__ import annotations

import unittest
from datetime import date

from landscape.planning import build_deterministic_query_plan, validate_query_plan_scope
from landscape.schemas import (
    AnalysisMode,
    CompetitorInput,
    LandscapePlannedQuery,
    LandscapeQueryPlan,
    LandscapeScope,
)


class LandscapePlanningTests(unittest.TestCase):
    def test_deterministic_plan_is_bounded_and_retains_direction(self) -> None:
        scope = LandscapeScope(
            mode=AnalysisMode.TECHNOLOGY,
            technology_direction="数据中心液冷",
            competitors=[CompetitorInput(name="Huawei", aliases=["华为"])],
            publication_start=date(2026, 4, 1),
            publication_end=date(2026, 7, 1),
        )
        plan = build_deterministic_query_plan(scope)
        self.assertGreaterEqual(len(plan.queries), 2)
        self.assertLessEqual(len(plan.queries), 6)
        validate_query_plan_scope(plan, scope)

    def test_plan_without_user_scope_anchor_is_rejected(self) -> None:
        scope = LandscapeScope(
            mode=AnalysisMode.COMPETITOR,
            competitors=[CompetitorInput(name="Huawei", aliases=["华为"])],
            publication_start=date(2026, 4, 1),
            publication_end=date(2026, 7, 1),
        )
        plan = LandscapeQueryPlan(
            queries=[
                LandscapePlannedQuery(query_text="Samsung", language="en", rationale="x"),
                LandscapePlannedQuery(query_text="Apple", language="en", rationale="x"),
            ]
        )
        with self.assertRaisesRegex(ValueError, "confirmed competitor"):
            validate_query_plan_scope(plan, scope)


if __name__ == "__main__":
    unittest.main()
