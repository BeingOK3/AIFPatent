from __future__ import annotations

import unittest

from landscape.query_planning import build_query_plan
from landscape.scale_gate import ScaleGateError, ScaleTier, estimate_scale, requires_confirmation
from tests.test_landscape_query_planning import confirmed_scope


class LandscapeScaleGateTests(unittest.TestCase):
    def setUp(self):
        self.plan = build_query_plan(confirmed_scope(companies=(("华为", ("华为",)),)))

    def test_default_boundary_is_not_truncated(self):
        estimate = estimate_scale(self.plan, (500,))
        self.assertEqual(estimate.tier, ScaleTier.WITHIN_DEFAULT)
        self.assertEqual(estimate.estimated_total_results, 500)
        self.assertFalse(requires_confirmation(estimate))

    def test_medium_and_large_require_confirmation(self):
        medium = estimate_scale(self.plan, (501,))
        large = estimate_scale(self.plan, (1001,))
        self.assertEqual(medium.tier, ScaleTier.CONFIRM_MEDIUM)
        self.assertEqual(large.tier, ScaleTier.CONFIRM_LARGE)
        self.assertTrue(requires_confirmation(medium))
        self.assertTrue(requires_confirmation(large))

    def test_multiple_queries_sum_totals_and_pages(self):
        plan = build_query_plan(confirmed_scope(companies=(("华为", ("华为", "Huawei")),)))
        estimate = estimate_scale(plan, (101, 199), page_size=100, shard_size=200)
        self.assertEqual(estimate.query_count, 2)
        self.assertEqual(estimate.estimated_total_results, 300)
        self.assertEqual(estimate.estimated_total_pages, 4)
        self.assertEqual(estimate.estimated_shards, 2)

    def test_missing_or_invalid_totals_fail_closed(self):
        with self.assertRaises(ScaleGateError):
            estimate_scale(self.plan, ())
        with self.assertRaises(ScaleGateError):
            estimate_scale(self.plan, (-1,))


if __name__ == "__main__":
    unittest.main()
