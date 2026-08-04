from __future__ import annotations

import unittest
from datetime import date

from landscape.metrics import MetricAnalysisUnit, MetricPublication, build_metric_cube
from landscape.mode_views import ViewAxis, build_mode_view
from landscape.scope import LandscapeInputMode


def unit(index: int, direction: str, organization: str):
    return MetricAnalysisUnit(
        analysis_unit_id=f"AU-{index:016x}",
        direction_id=direction,
        publications=(
            MetricPublication(
                publication_id=f"PUB-{index}",
                publication_number=f"CN{index}A",
                publication_date=date(2024, 1, index),
                primary_organization_id=organization,
            ),
        ),
        classification_confidence=0.8,
        evidence_completeness=1,
    )


class LandscapeModeViewTests(unittest.TestCase):
    def setUp(self):
        self.cube = build_metric_cube(
            (
                unit(1, "DIR-A", "ORG-A"),
                unit(2, "DIR-B", "ORG-A"),
                unit(3, "DIR-A", "ORG-B"),
                unit(4, "DIR-A", "ORG-UNCONFIRMED"),
            ),
            publication_start=date(2024, 1, 1),
            publication_end=date(2024, 2, 1),
        )

    def test_technology_only_is_direction_then_observed_organization(self):
        view = build_mode_view(
            self.cube,
            mode=LandscapeInputMode.TECHNOLOGY_ONLY,
        )
        self.assertEqual(view.primary_axis, ViewAxis.DIRECTION)
        self.assertEqual([group.group_id for group in view.groups], ["DIR-A", "DIR-B"])
        self.assertEqual(
            {leaf.organization_id for leaf in view.groups[0].leaves},
            {"ORG-A", "ORG-B", "ORG-UNCONFIRMED"},
        )
        self.assertFalse(view.comparison_enabled)

    def test_company_only_is_company_then_direction_and_filters_unconfirmed(self):
        view = build_mode_view(
            self.cube,
            mode=LandscapeInputMode.COMPANY_ONLY,
            confirmed_organization_ids=("ORG-A", "ORG-B"),
        )
        self.assertEqual(view.primary_axis, ViewAxis.ORGANIZATION)
        self.assertEqual([group.group_id for group in view.groups], ["ORG-A", "ORG-B"])
        self.assertTrue(view.comparison_enabled)
        self.assertEqual(view.unconfirmed_organization_ids, ("ORG-UNCONFIRMED",))
        self.assertNotIn(
            "ORG-UNCONFIRMED",
            {leaf.organization_id for group in view.groups for leaf in group.leaves},
        )

    def test_combined_is_direction_then_confirmed_company(self):
        view = build_mode_view(
            self.cube,
            mode=LandscapeInputMode.COMPANY_AND_TECHNOLOGY,
            confirmed_organization_ids=("ORG-A", "ORG-B"),
        )
        self.assertEqual(view.primary_axis, ViewAxis.DIRECTION)
        self.assertEqual(view.secondary_axis, ViewAxis.ORGANIZATION)
        self.assertEqual(
            {leaf.organization_id for group in view.groups for leaf in group.leaves},
            {"ORG-A", "ORG-B"},
        )

    def test_single_company_does_not_create_false_cross_company_comparison(self):
        view = build_mode_view(
            self.cube,
            mode=LandscapeInputMode.COMPANY_ONLY,
            confirmed_organization_ids=("ORG-A",),
        )
        self.assertFalse(view.comparison_enabled)
        self.assertEqual([group.group_id for group in view.groups], ["ORG-A"])

    def test_company_mode_requires_confirmed_scope(self):
        with self.assertRaisesRegex(ValueError, "require confirmed"):
            build_mode_view(self.cube, mode=LandscapeInputMode.COMPANY_ONLY)


if __name__ == "__main__":
    unittest.main()
