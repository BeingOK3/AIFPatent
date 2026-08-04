from __future__ import annotations

import hashlib
import unittest
from datetime import date

from landscape.macro_summary import (
    MacroSummary,
    build_macro_summary,
    classify_pulse,
)
from landscape.metrics import (
    MetricAnalysisUnit,
    MetricPublication,
    build_metric_cube,
)
from landscape.organization_assignment import (
    Organization,
    OrganizationAssignmentSet,
    OrganizationType,
)
from landscape.trends import (
    ConclusionStrength,
    TrendBucketMetric,
    TrendCandidate,
    TrendChangeType,
)


def org(display: str) -> Organization:
    identity = display
    org_id = f"ORG-{hashlib.sha256(identity.encode()).hexdigest()[:16]}"
    return Organization(
        organization_id=org_id,
        display_name=display,
        normalized_name=display,
        organization_type=OrganizationType.COMPANY,
        source_profile_id=identity,
        observed_names=(display,),
    )


def publication(index: int, published: date, primary: str) -> MetricPublication:
    return MetricPublication(
        publication_id=f"PUB-{index}",
        publication_number=f"CN{index}A",
        title=f"Patent {index}",
        publication_date=published,
        primary_organization_id=primary,
        co_organization_ids=(),
    )


def unit(index: int, direction: str, items: tuple[MetricPublication, ...]):
    return MetricAnalysisUnit(
        analysis_unit_id=f"AU-{index:016x}",
        direction_id=direction,
        publications=items,
        classification_confidence=0.9,
        evidence_completeness=1,
    )


def trend(direction_id: str, change: TrendChangeType) -> TrendCandidate:
    return TrendCandidate(
        candidate_id=f"TC-{hashlib.sha256(direction_id.encode()).hexdigest()[:16]}",
        policy_version="policy-1",
        direction_id=direction_id,
        organization_ids=("ORG-A",),
        bucket_metrics=(
            TrendBucketMetric(bucket_id="TB-1", analysis_unit_count=1, publication_count=1),
            TrendBucketMetric(bucket_id="TB-2", analysis_unit_count=2, publication_count=2),
        ),
        change_type=change,
        allowed_conclusion_strength=(
            ConclusionStrength.STRONG
            if change != TrendChangeType.CURRENT_LAYOUT
            else ConclusionStrength.OBSERVATION
        ),
        analysis_unit_ids=(f"AU-{hashlib.sha256(direction_id.encode()).hexdigest()[:16]}",),
        representative_analysis_unit_ids=(),
        evidence_ids=(),
        limitation_codes=(),
    )


class PulseLabelTests(unittest.TestCase):
    def test_growing_when_second_half_dominates(self) -> None:
        self.assertEqual(classify_pulse([1, 1, 4, 6]), "GROWING")

    def test_declining_when_first_half_dominates(self) -> None:
        self.assertEqual(classify_pulse([6, 4, 1, 1]), "DECLINING")

    def test_stable_when_balanced(self) -> None:
        self.assertEqual(classify_pulse([2, 3, 2, 3]), "STABLE")

    def test_uncertain_below_minimum_total(self) -> None:
        self.assertEqual(classify_pulse([1, 2]), "UNCERTAIN")

    def test_uncertain_single_bucket(self) -> None:
        self.assertEqual(classify_pulse([9]), "UNCERTAIN")


class MacroSummaryTests(unittest.TestCase):
    def _cube(self, org_a_id="ORG-A", org_b_id="ORG-B"):
        return build_metric_cube(
            (
                unit(
                    1,
                    "DIR-A",
                    (
                        publication(1, date(2024, 1, 10), org_a_id),
                        publication(2, date(2024, 4, 10), org_a_id),
                        publication(3, date(2024, 7, 10), org_a_id),
                    ),
                ),
                unit(
                    2,
                    "DIR-A",
                    (
                        publication(4, date(2024, 2, 10), org_b_id),
                        publication(5, date(2024, 8, 10), org_b_id),
                    ),
                ),
                unit(
                    3,
                    "DIR-B",
                    (
                        publication(6, date(2024, 3, 10), org_b_id),
                    ),
                ),
            ),
            publication_start=date(2024, 1, 1),
            publication_end=date(2024, 12, 31),
        )

    def test_build_macro_summary_aggregates_real_counts(self) -> None:
        acme = org("Acme")
        beta = org("Beta")
        cube = self._cube(org_a_id=acme.organization_id, org_b_id=beta.organization_id)
        organizations = OrganizationAssignmentSet(
            run_id="run-1",
            organizations=(org("Acme"), org("Beta")),
            assignments=(),
        )
        trends = (
            trend("DIR-A", TrendChangeType.STRENGTHENING),
            trend("DIR-B", TrendChangeType.CURRENT_LAYOUT),
        )
        summary = build_macro_summary(
            cube=cube,
            trends=trends,
            organizations=organizations,
            direction_name_by_id={"DIR-A": "液冷", "DIR-B": "风冷"},
        )

        self.assertIsInstance(summary, MacroSummary)
        self.assertEqual(summary.total_publications, 6)
        self.assertEqual(summary.total_analysis_units, 3)
        self.assertEqual(summary.direction_count, 2)
        self.assertEqual(summary.organization_count, 2)
        self.assertEqual(len(summary.overall_pulse), len(cube.buckets))
        self.assertEqual(sum(summary.overall_pulse, 0) if False else sum(point.analysis_unit_count for point in summary.overall_pulse), 3)
        self.assertEqual(summary.top_directions[0].direction_id, "DIR-A")
        self.assertEqual(summary.top_directions[0].name, "液冷")
        self.assertEqual(summary.top_directions[0].analysis_unit_count, 2)
        self.assertEqual(summary.top_directions[0].change_type, "STRENGTHENING")
        names = {item.organization_id: item.name for item in summary.top_organizations}
        self.assertIn("Acme", names.values())
        self.assertEqual(summary.change_distribution["STRENGTHENING"], 1)
        self.assertIn("3", summary.narrative)
        self.assertIn("液冷", summary.narrative)

    def test_pulse_uncertain_when_samples_insufficient(self) -> None:
        cube = build_metric_cube(
            (
                unit(
                    1,
                    "DIR-A",
                    (publication(1, date(2024, 1, 10), "ORG-A"),),
                ),
            ),
            publication_start=date(2024, 1, 1),
            publication_end=date(2024, 3, 31),
        )
        summary = build_macro_summary(
            cube=cube,
            trends=(),
            organizations=OrganizationAssignmentSet(
            run_id="run-1",
            organizations=(org("Acme"),),
            assignments=(),),
            direction_name_by_id={"DIR-A": "液冷"},
            minimum_pulse_total=5,
        )
        self.assertEqual(summary.pulse_direction, "UNCERTAIN")
        self.assertIn("不足", summary.narrative)


if __name__ == "__main__":
    unittest.main()
