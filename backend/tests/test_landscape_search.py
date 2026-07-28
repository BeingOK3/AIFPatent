from __future__ import annotations

import asyncio
import unittest
from datetime import date

from idea.providers.base import SearchHit, SearchProvider
from landscape.schemas import (
    AnalysisBudget,
    AnalysisMode,
    CompetitorInput,
    LandscapePlannedQuery,
    LandscapeQueryPlan,
    LandscapeScope,
)
from landscape.search import (
    assignee_matches_confirmed_competitor,
    execute_provider_queries,
    family_footprint,
    matched_competitor_name,
    scoped_provider_query_text,
    strict_filter_and_select,
    weighted_analysis_selection,
)


class TrackingProvider(SearchProvider):
    def __init__(self, name: str, delay: float):
        self.name = name
        self.delay = delay
        self.active = 0
        self.max_active = 0
        self.calls = 0

    async def search(self, query):
        self.calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delay)
            return []
        finally:
            self.active -= 1

    async def fetch(self, request):
        raise NotImplementedError


class FailingProvider(TrackingProvider):
    def __init__(self, name: str, error_code: str):
        super().__init__(name, 0)
        self.error_code = error_code

    async def search(self, query):
        self.calls += 1
        error = RuntimeError("provider unavailable")
        error.error_code = self.error_code
        raise error


def hit(
    rank: int,
    publication: str | None,
    published: str | None,
    *,
    assignee: str | None = "Example Corp",
    query_title: str = "Liquid cooling system",
    family_jurisdictions: list[str] | None = None,
    application_number: str | None = None,
    family_id: str | None = None,
) -> SearchHit:
    return SearchHit(
        provider="fixture",
        provider_rank=rank,
        title=query_title,
        url=f"https://example.test/{rank}",
        publication_number=publication,
        application_number=application_number,
        family_id=family_id,
        publication_date=published,
        assignee=assignee,
        raw={
            "country_status": {
                jurisdiction: "ACTIVE"
                for jurisdiction in (family_jurisdictions or [])
            }
        },
    )


class LandscapeSearchTests(unittest.TestCase):
    def technology_scope(self, candidate_limit: int = 100) -> LandscapeScope:
        return LandscapeScope(
            mode=AnalysisMode.TECHNOLOGY,
            technology_direction="liquid cooling",
            publication_start=date(2026, 4, 1),
            publication_end=date(2026, 6, 30),
            budget=AnalysisBudget(candidate_limit=candidate_limit, analysis_limit=10),
        )

    def test_window_boundaries_are_inclusive_and_unknown_dates_are_excluded(self) -> None:
        result = strict_filter_and_select(
            [
                (
                    "LQ-1",
                    [
                        hit(1, "US-1-A1", "2026-04-01"),
                        hit(2, "US-2-A1", "2026-06-30"),
                        hit(3, "US-3-A1", "2026-03-31"),
                        hit(4, "US-4-A1", None),
                        hit(5, "US-5-A1", "unknown"),
                        hit(6, None, "2026-05-01"),
                    ],
                )
            ],
            scope=self.technology_scope(),
        )
        self.assertEqual(
            [candidate.publication_number for candidate in result.candidates],
            ["US1A1", "US2A1"],
        )
        self.assertEqual(result.coverage.excluded_counts["PUBLICATION_DATE_OUTSIDE_WINDOW"], 1)
        self.assertEqual(result.coverage.excluded_counts["PUBLICATION_DATE_MISSING"], 1)
        self.assertEqual(result.coverage.excluded_counts["PUBLICATION_DATE_INVALID"], 1)
        self.assertEqual(result.coverage.excluded_counts["INVALID_PUBLICATION_NUMBER"], 1)

    def test_competitor_mode_uses_only_confirmed_aliases_and_avoids_substring_false_positive(self) -> None:
        scope = LandscapeScope(
            mode=AnalysisMode.COMPETITOR,
            competitors=[CompetitorInput(name="Meta", aliases=["元宇宙公司"])],
            publication_start=date(2026, 4, 1),
            publication_end=date(2026, 6, 30),
        )
        self.assertTrue(assignee_matches_confirmed_competitor("Meta Platforms, Inc.", scope))
        self.assertTrue(assignee_matches_confirmed_competitor("北京元宇宙公司有限公司", scope))
        self.assertFalse(assignee_matches_confirmed_competitor("Metallurgy Systems Ltd.", scope))
        result = strict_filter_and_select(
            [
                (
                    "LQ-1",
                    [
                        hit(1, "US-1-A1", "2026-05-01", assignee="Meta Platforms, Inc."),
                        hit(2, "US-2-A1", "2026-05-02", assignee="Metallurgy Systems Ltd."),
                        hit(3, "US-3-A1", "2026-05-03", assignee=None),
                    ],
                )
            ],
            scope=scope,
        )
        self.assertEqual([item.publication_number for item in result.candidates], ["US1A1"])
        self.assertEqual(result.coverage.excluded_counts["COMPETITOR_NOT_CONFIRMED"], 2)

    def test_candidate_limit_marks_complete_set_for_fail_closed_execution(self) -> None:
        batches = [
            (
                "LQ-1",
                [
                    hit(index, f"US-{index}-A1", "2026-05-01")
                    for index in range(1, 12)
                ],
            ),
            ("LQ-2", [hit(1, "US-1-A1", "2026-05-01")]),
        ]
        result = strict_filter_and_select(
            batches, scope=self.technology_scope(candidate_limit=10)
        )
        self.assertEqual(result.coverage.eligible_hit_count, 12)
        self.assertEqual(result.coverage.unique_candidate_count, 11)
        self.assertEqual(result.coverage.selected_count, 11)
        self.assertEqual(result.coverage.truncated_count, 0)
        self.assertTrue(result.coverage.candidate_limit_exceeded)
        self.assertEqual(len(result.candidates), 11)

    def test_complete_eligible_set_is_preserved_when_within_safety_bound(self) -> None:
        batches = [
            (
                "LQ-1",
                [
                    hit(index, f"US-{index}-A1", "2026-05-01")
                    for index in range(1, 10)
                ],
            ),
            ("LQ-2", [hit(1, "US-1-A1", "2026-05-01")]),
        ]
        result = strict_filter_and_select(
            batches, scope=self.technology_scope(candidate_limit=10)
        )
        self.assertEqual(result.coverage.eligible_hit_count, 10)
        self.assertEqual(result.coverage.unique_candidate_count, 9)
        self.assertEqual(result.coverage.selected_count, 9)
        self.assertEqual(result.coverage.truncated_count, 0)
        self.assertEqual(len(result.candidates), 9)
        self.assertEqual(result.candidates[0].query_ids, ["LQ-1", "LQ-2"])
        self.assertTrue(all(item.selected for item in result.ranking))

    def test_deduplication_uses_only_normalized_publication_number(self) -> None:
        result = strict_filter_and_select(
            [
                (
                    "LQ-1",
                    [
                        hit(
                            1,
                            "US-1-A1",
                            "2026-05-01",
                            application_number="APP-SHARED",
                            family_id="FAMILY-SHARED",
                        ),
                        hit(
                            2,
                            "EP-1-A1",
                            "2026-05-02",
                            application_number="APP-SHARED",
                            family_id="FAMILY-SHARED",
                        ),
                    ],
                ),
                (
                    "LQ-2",
                    [
                        hit(
                            1,
                            "US 1 A1",
                            "2026-05-01",
                            application_number="APP-OTHER",
                            family_id="FAMILY-OTHER",
                        )
                    ],
                ),
            ],
            scope=self.technology_scope(),
        )
        self.assertEqual(
            {candidate.publication_number for candidate in result.candidates},
            {"US1A1", "EP1A1"},
        )
        us = next(
            candidate
            for candidate in result.candidates
            if candidate.publication_number == "US1A1"
        )
        self.assertEqual(us.query_ids, ["LQ-1", "LQ-2"])

    def test_company_counts_use_competitor_primary_name_and_deep_order_is_balanced(self) -> None:
        scope = LandscapeScope(
            mode=AnalysisMode.COMPETITOR,
            competitors=[
                CompetitorInput(name="Huawei", aliases=["华为"]),
                CompetitorInput(name="Vertiv", aliases=["维谛"]),
            ],
            publication_start=date(2026, 4, 1),
            publication_end=date(2026, 6, 30),
            budget=AnalysisBudget(candidate_limit=10, analysis_limit=4),
        )
        batches = [
            (
                "LQ-1",
                [
                    hit(1, "CN-1-A", "2026-05-01", assignee="华为技术有限公司"),
                    hit(2, "CN-2-A", "2026-05-02", assignee="华为技术有限公司"),
                    hit(3, "CN-3-A", "2026-05-03", assignee="华为技术有限公司"),
                    hit(4, "US-4-A1", "2026-05-04", assignee="Vertiv Corporation"),
                    hit(5, "US-5-A1", "2026-05-05", assignee="Vertiv Corporation"),
                ],
            )
        ]
        result = strict_filter_and_select(batches, scope=scope)
        counts = {
            item.company: item.patent_count
            for item in result.coverage.company_patent_counts
        }
        self.assertEqual(counts, {"Huawei": 3, "Vertiv": 2})
        self.assertEqual(matched_competitor_name("华为技术有限公司", scope), "Huawei")
        ordered = weighted_analysis_selection(
            result.candidates,
            scope=scope,
            company_patent_counts=result.coverage.company_patent_counts,
        )
        self.assertEqual(
            [matched_competitor_name(item.assignee, scope) for item in ordered[:4]],
            ["Huawei", "Vertiv", "Huawei", "Huawei"],
        )

    def test_deep_review_guarantees_company_then_weights_count_and_family_footprint(self) -> None:
        scope = LandscapeScope(
            mode=AnalysisMode.COMPETITOR,
            competitors=[
                CompetitorInput(name="LargeCo"),
                CompetitorInput(name="SmallCo"),
            ],
            publication_start=date(2026, 4, 1),
            publication_end=date(2026, 6, 30),
            budget=AnalysisBudget(candidate_limit=10, analysis_limit=4),
        )
        batches = [
            (
                "LQ-1",
                [
                    hit(
                        index,
                        f"US-L{index}-A1",
                        "2026-05-01",
                        assignee="LargeCo",
                        family_jurisdictions=(
                            ["US", "EP", "CN"] if index == 2 else ["US"]
                        ),
                    )
                    for index in range(1, 7)
                ]
                + [
                    hit(
                        10 + index,
                        f"US-S{index}-A1",
                        "2026-05-01",
                        assignee="SmallCo",
                        family_jurisdictions=["US"],
                    )
                    for index in range(1, 3)
                ],
            )
        ]
        result = strict_filter_and_select(batches, scope=scope)
        ordered = weighted_analysis_selection(
            result.candidates,
            scope=scope,
            company_patent_counts=result.coverage.company_patent_counts,
        )
        companies = [matched_competitor_name(item.assignee, scope) for item in ordered[:4]]
        self.assertEqual(companies, ["LargeCo", "SmallCo", "LargeCo", "LargeCo"])
        self.assertEqual(ordered[0].publication_number, "USL2A1")
        self.assertEqual(family_footprint(ordered[0]), 3)
        large_rank = next(
            item for item in result.ranking if item.publication_number == "USL2A1"
        )
        self.assertEqual(large_rank.family_footprint, 3)
        self.assertIn("可核验同族法域 3 个", large_rank.reasons)

    def test_combined_mode_enforces_competitor_assignee_filter(self) -> None:
        scope = LandscapeScope(
            technology_direction="liquid cooling",
            competitors=[CompetitorInput(name="Huawei", aliases=["华为"])],
            publication_start=date(2026, 4, 1),
            publication_end=date(2026, 6, 30),
        )
        result = strict_filter_and_select(
            [
                (
                    "LQ-1",
                    [
                        hit(1, "US-1-A1", "2026-05-01", assignee="Huawei Technologies"),
                        hit(2, "US-2-A1", "2026-05-02", assignee="Other Corp"),
                    ],
                )
            ],
            scope=scope,
        )
        self.assertEqual([item.publication_number for item in result.candidates], ["US1A1"])
        self.assertEqual(scope.mode, AnalysisMode.TECHNOLOGY_COMPETITOR)

    def test_provider_query_receives_program_owned_publication_window_hints(self) -> None:
        text = scoped_provider_query_text("liquid cooling", self.technology_scope())
        self.assertIn("after=publication:20260331", text)
        self.assertIn("before=publication:20260701", text)
        exa = scoped_provider_query_text(
            "liquid cooling", self.technology_scope(), "exa_mcp"
        )
        self.assertIn("published from 2026-04-01 to 2026-06-30", exa)
        self.assertNotIn("after=publication", exa)

    def test_queries_are_serialized_per_provider(self) -> None:
        provider = TrackingProvider("fixture_serial", 0.01)
        plan = LandscapeQueryPlan(
            direction_terms=["liquid cooling"],
            direction_english_terms=["liquid cooling"],
            queries=[
                LandscapePlannedQuery(query_text="liquid cooling", language="en", rationale="one"),
                LandscapePlannedQuery(query_text='"liquid cooling"', language="en", rationale="two"),
            ],
        )
        results = asyncio.run(
            execute_provider_queries(
                scope=self.technology_scope(),
                plan=plan,
                providers=[provider],
                timeout_seconds={provider.name: 1},
            )
        )
        self.assertEqual(provider.calls, 2)
        self.assertEqual(provider.max_active, 1)
        self.assertEqual([item.status.value for item in results], ["EMPTY", "EMPTY"])

    def test_google_timeout_opens_run_scoped_circuit(self) -> None:
        provider = TrackingProvider("google_patents_local", 0.1)
        plan = LandscapeQueryPlan(
            direction_terms=["liquid cooling"],
            direction_english_terms=["liquid cooling"],
            queries=[
                LandscapePlannedQuery(query_text="liquid cooling", language="en", rationale="one"),
                LandscapePlannedQuery(query_text='"liquid cooling"', language="en", rationale="two"),
            ],
        )
        results = asyncio.run(
            execute_provider_queries(
                scope=self.technology_scope(),
                plan=plan,
                providers=[provider],
                timeout_seconds={provider.name: 0.01},
            )
        )
        self.assertEqual(provider.calls, 1)
        self.assertEqual([item.status.value for item in results], ["TIMEOUT", "DISABLED"])

    def test_serpapi_missing_key_opens_run_scoped_circuit(self) -> None:
        provider = FailingProvider(
            "serpapi_google_patents", "SERPAPI_API_KEY_REQUIRED"
        )
        plan = LandscapeQueryPlan(
            direction_terms=["liquid cooling"],
            direction_english_terms=["liquid cooling"],
            queries=[
                LandscapePlannedQuery(
                    query_text="liquid cooling", language="en", rationale="one"
                ),
                LandscapePlannedQuery(
                    query_text='"liquid cooling"', language="en", rationale="two"
                ),
            ],
        )
        results = asyncio.run(
            execute_provider_queries(
                scope=self.technology_scope(),
                plan=plan,
                providers=[provider],
                timeout_seconds={provider.name: 1},
            )
        )
        self.assertEqual(provider.calls, 1)
        self.assertEqual([item.status.value for item in results], ["ERROR", "DISABLED"])

    def test_exa_rate_limit_opens_run_scoped_circuit(self) -> None:
        provider = FailingProvider("exa_mcp", "HTTPStatusError")

        async def rate_limited(_query):
            provider.calls += 1
            error = RuntimeError("429 Too Many Requests")
            error.error_code = "HTTPStatusError"
            raise error

        provider.search = rate_limited
        plan = LandscapeQueryPlan(
            direction_terms=["liquid cooling"],
            direction_english_terms=["liquid cooling"],
            queries=[
                LandscapePlannedQuery(
                    query_text="liquid cooling", language="en", rationale="one"
                ),
                LandscapePlannedQuery(
                    query_text='"liquid cooling"', language="en", rationale="two"
                ),
            ],
        )
        results = asyncio.run(
            execute_provider_queries(
                scope=self.technology_scope(),
                plan=plan,
                providers=[provider],
                timeout_seconds={provider.name: 1},
            )
        )
        self.assertEqual(provider.calls, 1)
        self.assertEqual([item.status.value for item in results], ["ERROR", "DISABLED"])
        self.assertEqual(results[1].error_code, "EXA_RATE_LIMITED")


if __name__ == "__main__":
    unittest.main()
