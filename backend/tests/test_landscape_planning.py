from __future__ import annotations

import unittest
from datetime import date

from landscape.planning import (
    CompetitorAliasError,
    build_deterministic_query_plan,
    fallback_alias_plan,
    scope_with_alias_plan,
    validate_alias_plan,
    validate_query_plan_scope,
)
from landscape.schemas import (
    AnalysisMode,
    CompetitorAliasPlan,
    CompetitorAliasResolution,
    CompetitorInput,
    LandscapePlannedQuery,
    LandscapeQueryPlan,
    LandscapeScope,
)


class LandscapePlanningTests(unittest.TestCase):
    def test_deterministic_plan_is_bounded_and_retains_direction(self) -> None:
        scope = LandscapeScope(
            mode=AnalysisMode.TECHNOLOGY_COMPETITOR,
            technology_direction="数据中心液冷",
            competitors=[CompetitorInput(name="Huawei", aliases=["华为"])],
            publication_start=date(2026, 4, 1),
            publication_end=date(2026, 7, 1),
        )
        plan = build_deterministic_query_plan(scope)
        self.assertGreaterEqual(len(plan.queries), 2)
        self.assertLessEqual(len(plan.queries), 6)
        validate_query_plan_scope(plan, scope)

    def test_combined_mode_plan_must_retain_both_anchors(self) -> None:
        scope = LandscapeScope(
            technology_direction="数据中心液冷",
            competitors=[CompetitorInput(name="Huawei")],
            publication_start=date(2026, 4, 1),
            publication_end=date(2026, 7, 1),
        )
        plan = LandscapeQueryPlan(
            queries=[
                LandscapePlannedQuery(query_text="数据中心液冷", language="zh", rationale="x"),
                LandscapePlannedQuery(query_text="liquid cooling", language="en", rationale="x"),
            ]
        )
        with self.assertRaisesRegex(ValueError, "confirmed competitor"):
            validate_query_plan_scope(plan, scope)

    def test_alias_plan_cannot_change_or_cross_competitor_entities(self) -> None:
        inputs = [CompetitorInput(name="Huawei"), CompetitorInput(name="Samsung")]
        valid = CompetitorAliasPlan(
            competitors=[
                CompetitorAliasResolution(
                    primary_name="Huawei", aliases=["华为"], source="MODEL_INFERRED"
                ),
                CompetitorAliasResolution(
                    primary_name="Samsung", aliases=["三星"], source="MODEL_INFERRED"
                ),
            ]
        )
        validate_alias_plan(valid, inputs)
        effective = scope_with_alias_plan(
            LandscapeScope(
                competitors=inputs,
                publication_start=date(2026, 4, 1),
                publication_end=date(2026, 7, 1),
            ),
            valid,
        )
        self.assertEqual(effective.competitors[0].aliases, ["华为"])
        collision = valid.model_copy(
            update={
                "competitors": [
                    valid.competitors[0].model_copy(update={"aliases": ["Samsung"]}),
                    valid.competitors[1],
                ]
            }
        )
        with self.assertRaisesRegex(CompetitorAliasError, "collides"):
            validate_alias_plan(collision, inputs)

    def test_alias_failure_falls_back_to_primary_names(self) -> None:
        fallback = fallback_alias_plan([CompetitorInput(name="Huawei")])
        self.assertEqual(fallback.competitors[0].aliases, [])
        self.assertEqual(fallback.competitors[0].source, "PRIMARY_NAME_FALLBACK")

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
