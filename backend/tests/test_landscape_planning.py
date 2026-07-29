from __future__ import annotations

import asyncio
import unittest
from datetime import date
from types import SimpleNamespace

from landscape.planning import (
    ALIAS_AGENT_NAME,
    CompetitorAliasService,
)
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
    AssigneeScope,
    CompetitorAliasPlan,
    CompetitorAliasResolution,
    CompetitorInput,
    LandscapePlannedQuery,
    LandscapeQueryPlan,
    LandscapeScope,
    TechnicalDirectionExpansion,
)


def direction_expansion() -> TechnicalDirectionExpansion:
    return TechnicalDirectionExpansion(
        original_term="数据中心液冷",
        chinese_terms=["数据中心液冷", "冷板液冷", "浸没式液冷"],
        english_terms=["data center liquid cooling", "cold plate cooling", "immersion cooling"],
        source="MODEL_INFERRED",
    )


class LandscapePlanningTests(unittest.TestCase):
    def test_alias_model_output_is_reconciled_to_requested_company_names(self) -> None:
        class StubModel:
            async def complete(self, agent_name, *, system_prompt, input_payload):
                self.call = (agent_name, input_payload)
                return SimpleNamespace(
                    output=CompetitorAliasPlan(
                        competitors=[
                            CompetitorAliasResolution(
                                primary_name="NVIDIA",
                                aliases=["英伟达", "NVIDIA Corporation"],
                                source="MODEL_INFERRED",
                            )
                        ]
                    )
                )

        model = StubModel()
        service = CompetitorAliasService(model)

        result = asyncio.run(
            service.resolve([CompetitorInput(name="英伟达", aliases=[])])
        )

        self.assertEqual(model.call[0], ALIAS_AGENT_NAME)
        self.assertEqual(result.competitors[0].primary_name, "英伟达")
        self.assertEqual(result.competitors[0].assignee_scope, AssigneeScope.ENTITY)
        self.assertEqual(
            result.competitors[0].aliases,
            ["NVIDIA", "NVIDIA Corporation"],
        )

    def test_alias_model_missing_row_falls_back_only_for_that_company(self) -> None:
        class StubModel:
            async def complete(self, agent_name, *, system_prompt, input_payload):
                return SimpleNamespace(
                    output=CompetitorAliasPlan(
                        competitors=[
                            CompetitorAliasResolution(
                                primary_name="NVIDIA",
                                aliases=["英伟达"],
                                source="MODEL_INFERRED",
                            )
                        ]
                    )
                )

        result = asyncio.run(
            CompetitorAliasService(StubModel()).resolve(
                [
                    CompetitorInput(name="英伟达", aliases=[]),
                    CompetitorInput(name="华为", aliases=[]),
                ]
            )
        )

        self.assertEqual(
            [(item.primary_name, item.source) for item in result.competitors],
            [("英伟达", "MODEL_INFERRED"), ("华为", "PRIMARY_NAME_FALLBACK")],
        )

    def test_deterministic_plan_is_bounded_and_retains_direction(self) -> None:
        scope = LandscapeScope(
            mode=AnalysisMode.TECHNOLOGY_COMPETITOR,
            technology_direction="数据中心液冷",
            competitors=[CompetitorInput(name="Huawei", aliases=["华为"])],
            publication_start=date(2026, 4, 1),
            publication_end=date(2026, 7, 1),
        )
        plan = build_deterministic_query_plan(scope, direction_expansion())
        self.assertEqual(len(plan.queries), 1)
        self.assertEqual(plan.queries[0].language, "mixed")
        self.assertIn("data center liquid cooling", plan.queries[0].query_text)
        self.assertIn("数据中心液冷", plan.queries[0].query_text)
        validate_query_plan_scope(plan, scope)

    def test_every_competitor_receives_one_bilingual_combined_query(self) -> None:
        competitors = [
            CompetitorInput(name="中科曙光", aliases=["Sugon"]),
            CompetitorInput(name="华为", aliases=["Huawei"]),
            CompetitorInput(name="英伟达", aliases=["NVIDIA"]),
            CompetitorInput(name="浪潮", aliases=["Inspur"]),
        ]
        scope = LandscapeScope(
            technology_direction="数据中心液冷",
            competitors=competitors,
            publication_start=date(2026, 4, 1),
            publication_end=date(2026, 7, 1),
        )
        plan = build_deterministic_query_plan(scope, direction_expansion())
        self.assertEqual(len(plan.queries), 4)
        for competitor in competitors:
            queries = [
                item for item in plan.queries if competitor.name in item.rationale
            ]
            self.assertEqual(len(queries), 1, competitor.name)
            self.assertEqual(queries[0].language, "mixed")
            self.assertIn(competitor.name, queries[0].query_text)
            self.assertIn("data center liquid cooling", queries[0].query_text)
            self.assertIn("数据中心液冷", queries[0].query_text)
        validate_query_plan_scope(plan, scope)

    def test_competitor_only_plan_covers_every_competitor(self) -> None:
        scope = LandscapeScope(
            competitors=[
                CompetitorInput(name="Huawei", aliases=["华为"]),
                CompetitorInput(name="Samsung", aliases=["三星"]),
                CompetitorInput(name="NVIDIA", aliases=["英伟达"]),
                CompetitorInput(name="Inspur", aliases=["浪潮"]),
            ],
            publication_start=date(2026, 4, 1),
            publication_end=date(2026, 7, 1),
        )
        plan = build_deterministic_query_plan(scope)
        self.assertEqual(len(plan.queries), 4)
        for competitor in scope.competitors:
            self.assertEqual(
                sum(competitor.name in item.query_text for item in plan.queries), 1
            )
            matching = [
                item.query_text
                for item in plan.queries
                if competitor.name in item.query_text
            ]
            self.assertNotIn("assignee:", matching[0])
            for name in [competitor.name, *competitor.aliases]:
                self.assertIn(f'"{name}"', matching[0])
        validate_query_plan_scope(plan, scope)

    def test_combined_mode_plan_must_retain_both_anchors(self) -> None:
        scope = LandscapeScope(
            technology_direction="数据中心液冷",
            competitors=[CompetitorInput(name="Huawei")],
            publication_start=date(2026, 4, 1),
            publication_end=date(2026, 7, 1),
        )
        plan = LandscapeQueryPlan(
            direction_terms=["数据中心液冷", "liquid cooling"],
            direction_english_terms=["liquid cooling"],
            queries=[
                LandscapePlannedQuery(query_text="数据中心液冷", language="zh", rationale="x"),
                LandscapePlannedQuery(query_text="liquid cooling", language="en", rationale="x"),
            ]
        )
        with self.assertRaisesRegex(ValueError, "competitor: Huawei"):
            validate_query_plan_scope(plan, scope)

    def test_alias_plan_cannot_change_or_cross_competitor_entities(self) -> None:
        inputs = [
            CompetitorInput(name="Huawei", aliases=["华为"]),
            CompetitorInput(name="Samsung"),
        ]
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

    def test_search_scope_preserves_confirmed_aliases_and_adds_inferred_aliases(self) -> None:
        scope = LandscapeScope(
            competitors=[
                CompetitorInput(
                    name="Huawei",
                    aliases=["华为", "Huawei Technologies"],
                    assignee_scope=AssigneeScope.GROUP,
                )
            ],
            publication_start=date(2026, 4, 1),
            publication_end=date(2026, 7, 1),
        )
        plan = CompetitorAliasPlan(
            competitors=[
                CompetitorAliasResolution(
                    primary_name="Huawei",
                    aliases=["Huawei Technologies", "华为技术"],
                    source="MODEL_INFERRED",
                )
            ]
        )

        search_scope = scope_with_alias_plan(scope, plan)

        self.assertEqual(
            search_scope.competitors[0].aliases,
            ["华为", "Huawei Technologies", "华为技术"],
        )
        self.assertEqual(scope.competitors[0].aliases, ["华为", "Huawei Technologies"])
        self.assertEqual(
            search_scope.competitors[0].assignee_scope,
            AssigneeScope.GROUP,
        )

    def test_alias_failure_falls_back_to_primary_names(self) -> None:
        fallback = fallback_alias_plan(
            [CompetitorInput(name="Huawei", assignee_scope=AssigneeScope.GROUP)]
        )
        self.assertEqual(fallback.competitors[0].aliases, [])
        self.assertEqual(fallback.competitors[0].source, "PRIMARY_NAME_FALLBACK")
        self.assertEqual(fallback.competitors[0].assignee_scope, AssigneeScope.GROUP)

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
        with self.assertRaisesRegex(ValueError, "competitor: Huawei"):
            validate_query_plan_scope(plan, scope)


if __name__ == "__main__":
    unittest.main()
