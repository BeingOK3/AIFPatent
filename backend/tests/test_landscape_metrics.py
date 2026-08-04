from __future__ import annotations

import unittest
from datetime import date

from landscape.metrics import (
    MetricAnalysisUnit,
    MetricPublication,
    OrganizationCountingMode,
    TimeBucketGranularity,
    build_metric_cube,
    make_time_buckets,
)


def publication(
    index: int,
    published: date,
    primary: str = "ORG-A",
    co: tuple[str, ...] = (),
) -> MetricPublication:
    return MetricPublication(
        publication_id=f"PUB-{index}",
        publication_number=f"CN{index}A",
        title=f"Patent {index}",
        publication_date=published,
        primary_organization_id=primary,
        co_organization_ids=co,
    )


def unit(index: int, direction: str, publications: tuple[MetricPublication, ...]):
    return MetricAnalysisUnit(
        analysis_unit_id=f"AU-{index:016x}",
        direction_id=direction,
        publications=publications,
        classification_confidence=0.8,
        evidence_completeness=1,
    )


class LandscapeMetricTests(unittest.TestCase):
    def test_auto_bucket_boundaries_are_inclusive_and_calendar_aligned(self):
        monthly = make_time_buckets(date(2024, 1, 15), date(2024, 7, 14))
        self.assertTrue(all(item.granularity == TimeBucketGranularity.MONTH for item in monthly))
        self.assertEqual(monthly[0].start, date(2024, 1, 15))
        self.assertEqual(monthly[-1].end, date(2024, 7, 14))
        quarterly = make_time_buckets(date(2024, 1, 1), date(2024, 7, 1))
        self.assertTrue(all(item.granularity == TimeBucketGranularity.QUARTER for item in quarterly))
        yearly = make_time_buckets(date(2022, 1, 1), date(2024, 1, 1))
        self.assertTrue(all(item.granularity == TimeBucketGranularity.YEAR for item in yearly))

    def test_family_unit_counts_once_at_earliest_publication_but_publications_count_individually(self):
        cube = build_metric_cube(
            (
                unit(
                    1,
                    "DIR-A",
                    (
                        publication(1, date(2023, 1, 10)),
                        publication(2, date(2024, 2, 10)),
                    ),
                ),
            ),
            publication_start=date(2023, 1, 1),
            publication_end=date(2025, 1, 1),
        )
        self.assertEqual(cube.analysis_unit_count, 1)
        self.assertEqual(cube.publication_count, 2)
        self.assertEqual(sum(cell.analysis_unit_count for cell in cube.cells), 1)
        self.assertEqual(sum(cell.publication_count for cell in cube.cells), 2)
        self.assertEqual(cube.analysis_unit_time_policy, "EARLIEST_PUBLICATION_IN_FAMILY")

    def test_primary_and_all_known_organization_views_have_explicit_different_counts(self):
        units = (
            unit(
                1,
                "DIR-A",
                (publication(1, date(2024, 1, 1), "ORG-A", ("ORG-B",)),),
            ),
        )
        primary = build_metric_cube(
            units,
            publication_start=date(2024, 1, 1),
            publication_end=date(2024, 2, 1),
        )
        all_known = build_metric_cube(
            units,
            publication_start=date(2024, 1, 1),
            publication_end=date(2024, 2, 1),
            organization_counting_mode=OrganizationCountingMode.ALL_KNOWN,
        )
        self.assertEqual({cell.organization_id for cell in primary.cells}, {"ORG-A"})
        self.assertEqual(
            {cell.organization_id for cell in all_known.cells},
            {"ORG-A", "ORG-B"},
        )

    def test_direction_share_is_programmatic_and_recomputable(self):
        cube = build_metric_cube(
            (
                unit(1, "DIR-A", (publication(1, date(2024, 1, 1)),)),
                unit(2, "DIR-A", (publication(2, date(2024, 1, 2)),)),
                unit(3, "DIR-B", (publication(3, date(2024, 1, 3)),)),
            ),
            publication_start=date(2024, 1, 1),
            publication_end=date(2024, 2, 1),
        )
        cells = {cell.direction_id: cell for cell in cube.cells if cell.analysis_unit_count}
        self.assertAlmostEqual(cells["DIR-A"].direction_share, 2 / 3)
        self.assertAlmostEqual(cells["DIR-B"].direction_share, 1 / 3)

    def test_out_of_range_data_is_reported_and_duplicate_membership_fails(self):
        cube = build_metric_cube(
            (unit(1, "DIR-A", (publication(1, date(2020, 1, 1)),)),),
            publication_start=date(2024, 1, 1),
            publication_end=date(2024, 12, 31),
        )
        self.assertEqual(cube.analysis_unit_count, 0)
        self.assertEqual(cube.excluded_out_of_range_publication_count, 1)
        with self.assertRaisesRegex(ValueError, "more than one"):
            build_metric_cube(
                (
                    unit(1, "DIR-A", (publication(1, date(2024, 1, 1)),)),
                    unit(2, "DIR-B", (publication(1, date(2024, 1, 1)),)),
                ),
                publication_start=date(2024, 1, 1),
                publication_end=date(2024, 12, 31),
            )


if __name__ == "__main__":
    unittest.main()
