from __future__ import annotations

import unittest
from datetime import date

from landscape.metrics import MetricAnalysisUnit, MetricPublication, build_metric_cube
from landscape.trends import (
    ConclusionStrength,
    TrendChangeType,
    TrendNarrativeProposal,
    apply_trend_narrative,
    build_trend_candidates,
    deterministic_trend_narrative,
)


def units_for_counts(counts: tuple[int, ...]) -> tuple[MetricAnalysisUnit, ...]:
    years = (2022, 2023, 2024)
    result = []
    index = 1
    for year, count in zip(years, counts, strict=True):
        for _ in range(count):
            result.append(
                MetricAnalysisUnit(
                    analysis_unit_id=f"AU-{index:016x}",
                    direction_id="DIR-A",
                    publications=(
                        MetricPublication(
                            publication_id=f"PUB-{index}",
                            publication_number=f"CN{index}A",
                            publication_date=date(year, 6, 1),
                            primary_organization_id="ORG-A",
                        ),
                    ),
                    classification_confidence=0.5 + index / 100,
                    evidence_completeness=1,
                    direction_centrality=0.5,
                    evidence_ids=(f"EV-{index}",),
                )
            )
            index += 1
    return tuple(result)


def candidates_for_counts(counts: tuple[int, ...]):
    units = units_for_counts(counts)
    cube = build_metric_cube(
        units,
        publication_start=date(2022, 1, 1),
        publication_end=date(2024, 12, 31),
    )
    return build_trend_candidates(cube, units)


class LandscapeTrendTests(unittest.TestCase):
    def test_program_detects_new_strengthening_weakening_and_sustained(self):
        cases = {
            (0, 0, 6): TrendChangeType.NEW,
            (1, 1, 4): TrendChangeType.STRENGTHENING,
            (4, 1, 1): TrendChangeType.WEAKENING,
            (2, 2, 2): TrendChangeType.SUSTAINED_ACTIVE,
        }
        for counts, expected in cases.items():
            with self.subTest(counts=counts):
                candidate = candidates_for_counts(counts)[0]
                self.assertEqual(candidate.change_type, expected)
                self.assertEqual(
                    candidate.allowed_conclusion_strength,
                    ConclusionStrength.STRONG,
                )
                self.assertEqual(
                    [metric.analysis_unit_count for metric in candidate.bucket_metrics],
                    list(counts),
                )

    def test_insufficient_sample_is_only_current_layout_observation(self):
        candidate = candidates_for_counts((0, 1, 1))[0]
        self.assertEqual(candidate.change_type, TrendChangeType.CURRENT_LAYOUT)
        self.assertEqual(
            candidate.allowed_conclusion_strength,
            ConclusionStrength.OBSERVATION,
        )
        self.assertIn("INSUFFICIENT_ANALYSIS_UNITS", candidate.limitation_codes)

    def test_model_cannot_change_program_type_or_strengthen_observation(self):
        candidate = candidates_for_counts((0, 1, 1))[0]
        valid = TrendNarrativeProposal(
            candidate_id=candidate.candidate_id,
            change_type=candidate.change_type,
            conclusion_strength=ConclusionStrength.OBSERVATION,
            narrative="当前样本只支持布局观察。",
        )
        self.assertEqual(apply_trend_narrative(candidate, valid).source, "MODEL")
        with self.assertRaisesRegex(ValueError, "change"):
            apply_trend_narrative(
                candidate,
                valid.model_copy(update={"change_type": TrendChangeType.NEW}),
            )
        with self.assertRaisesRegex(ValueError, "strengthen"):
            apply_trend_narrative(
                candidate,
                valid.model_copy(update={"conclusion_strength": ConclusionStrength.STRONG}),
            )

    def test_narration_failure_has_deterministic_fallback(self):
        candidate = candidates_for_counts((1, 1, 4))[0]
        first = deterministic_trend_narrative(candidate)
        second = deterministic_trend_narrative(candidate)
        self.assertEqual(first, second)
        self.assertEqual(first.source, "DETERMINISTIC")
        self.assertIn("[1, 1, 4]", first.narrative)

    def test_candidate_members_and_evidence_are_program_bound(self):
        units = units_for_counts((2, 2, 2))
        cube = build_metric_cube(
            units,
            publication_start=date(2022, 1, 1),
            publication_end=date(2024, 12, 31),
        )
        first = build_trend_candidates(cube, units)
        second = build_trend_candidates(cube, tuple(reversed(units)))
        self.assertEqual(first, second)
        candidate = first[0]
        self.assertEqual(set(candidate.analysis_unit_ids), {unit.analysis_unit_id for unit in units})
        self.assertEqual(set(candidate.evidence_ids), {f"EV-{index}" for index in range(1, 7)})


if __name__ == "__main__":
    unittest.main()
