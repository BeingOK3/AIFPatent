from __future__ import annotations

import unittest
from datetime import date

from pydantic import ValidationError

from idea.config import load_config
from idea.merge import MergedHit, SourceRecord
from idea.search_strategy import (
    RoundStats,
    SaturationTracker,
    ScopeBreadth,
    StopReason,
    assess_breadth,
    build_budget,
    screen_summaries,
    select_deep_review,
)


def merged(publication, title, snippet, publication_date="2020-01-01"):
    return MergedHit(
        merge_key=f"publication:{publication}",
        publication_number=publication,
        title=title,
        snippet=snippet,
        publication_date=publication_date,
        found_by=["exa_mcp"],
        query_ids=["Q1"],
        sources=[
            SourceRecord(
                provider="exa_mcp", provider_rank=1, query_id="Q1", url="https://example.test", raw={}
            )
        ],
    )


class SearchStrategyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = load_config().search

    def test_narrow_scope_uses_minimum_target(self) -> None:
        breadth = assess_breadth(
            technical_domains=["KV cache"],
            features=["token heat score", "segmented eviction", "threshold update", "GPU memory"],
            search_terms=["token heat score", "segmented KV cache eviction", "dynamic threshold"],
        )
        budget = build_budget(self.settings, breadth, mode_name="standard")
        self.assertEqual(breadth, ScopeBreadth.NARROW)
        self.assertEqual(budget.deep_review_target, 10)

    def test_broad_scope_uses_configured_maximum_target(self) -> None:
        breadth = assess_breadth(
            technical_domains=["storage", "network", "machine learning"],
            features=["optimize data"],
            search_terms=["cache", "system"],
        )
        budget = build_budget(self.settings, breadth, mode_name="standard")
        self.assertEqual(breadth, ScopeBreadth.BROAD)
        self.assertEqual(budget.deep_review_target, 20)

    def test_user_caps_are_validated_and_never_lower_minimum_below_ten(self) -> None:
        budget = build_budget(
            self.settings,
            ScopeBreadth.MEDIUM,
            candidate_max=40,
            deep_review_min=10,
            deep_review_max=16,
        )
        self.assertEqual(budget.candidate_max, 40)
        self.assertEqual(budget.deep_review_target, 13)
        with self.assertRaises(ValidationError):
            build_budget(
                self.settings,
                ScopeBreadth.NARROW,
                candidate_max=20,
                deep_review_min=9,
                deep_review_max=10,
            )

    def test_saturation_requires_consecutive_low_yield_rounds(self) -> None:
        tracker = SaturationTracker(2, 1, 80)
        self.assertIsNone(tracker.add(RoundStats(1, 30, 20, 8, 2)))
        self.assertIsNone(tracker.add(RoundStats(2, 40, 10, 1, 2)))
        self.assertEqual(
            tracker.add(RoundStats(3, 42, 2, 0, 1)), StopReason.SATURATED
        )

    def test_candidate_cap_and_provider_failure_are_explicit_stop_reasons(self) -> None:
        tracker = SaturationTracker(2, 1, 40)
        self.assertEqual(tracker.add(RoundStats(1, 40, 40, 12, 2)), StopReason.CANDIDATE_MAX)
        tracker = SaturationTracker(2, 1, 80)
        self.assertEqual(
            tracker.add(RoundStats(1, 0, 0, 0, 0)), StopReason.PROVIDERS_UNAVAILABLE
        )

    def test_summary_screening_excludes_post_date_and_does_not_pad_weak_results(self) -> None:
        hits = [
            merged(f"US{i}A1", "cache eviction controller", "token heat threshold")
            for i in range(8)
        ]
        hits.extend(
            [
                merged("US-WEAK-A1", "unrelated hinge", "mechanical door"),
                merged(
                    "US-LATE-A1",
                    "cache eviction controller",
                    "token heat threshold",
                    publication_date="2027-01-01",
                ),
            ]
        )
        screened = screen_summaries(
            hits,
            idea_terms=["cache eviction", "token heat", "threshold"],
            evaluation_date=date(2026, 7, 16),
        )
        budget = build_budget(self.settings, ScopeBreadth.NARROW, mode_name="standard")
        selection = select_deep_review(screened, budget)
        self.assertEqual(len(selection.selected), 8)
        self.assertEqual(selection.limitation["code"], "INSUFFICIENT_RELEVANT_DEEP_REVIEWS")
        self.assertNotIn("US-WEAK-A1", [item.hit.publication_number for item in selection.selected])
        self.assertNotIn("US-LATE-A1", [item.hit.publication_number for item in selection.selected])

    def test_bilingual_term_groups_require_two_concepts_not_one_generic_match(self) -> None:
        hits = [
            merged(
                "CN-STRONG-A1",
                "自适应电池充电控制",
                "采集电芯温度和内阻并动态调整充电电流",
            ),
            merged("CN-WEAK-A1", "电池外壳", "一种通用电池结构"),
        ]
        screened = screen_summaries(
            hits,
            idea_terms=["充电控制", "adaptive charging", "电芯温度", "cell temperature"],
            term_groups=[
                ["充电控制", "adaptive charging"],
                ["电芯温度", "cell temperature", "内阻", "internal resistance"],
            ],
            evaluation_date=date(2026, 7, 16),
        )
        scores = {item.hit.publication_number: item.relevance_score for item in screened}
        self.assertGreaterEqual(scores["CN-STRONG-A1"], 0.15)
        self.assertLess(scores["CN-WEAK-A1"], 0.15)


if __name__ == "__main__":
    unittest.main()
