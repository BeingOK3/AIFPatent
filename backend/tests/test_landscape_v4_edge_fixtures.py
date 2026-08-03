from __future__ import annotations

import unittest

from backend.tests.landscape_v4_fixture_factory import (
    make_classification_terminal_case,
    make_family_case,
)


class LandscapeV4EdgeFixtureTests(unittest.TestCase):
    def test_family_fixture_has_no_missing_or_duplicate_member(self) -> None:
        case = make_family_case()
        publication_ids = {
            publication["publication_id"] for publication in case["publications"]
        }
        grouped_ids = [
            publication_id
            for members in case["expected_analysis_units"].values()
            for publication_id in members
        ]
        self.assertEqual(set(grouped_ids), publication_ids)
        self.assertEqual(len(grouped_ids), len(set(grouped_ids)))

    def test_family_fixture_freezes_conservative_merge_boundaries(self) -> None:
        groups = make_family_case()["expected_analysis_units"]
        self.assertEqual(len(groups["AU-SAME-APPLICATION"]), 2)
        self.assertEqual(len(groups["AU-SIMPLE-FAMILY"]), 2)
        self.assertEqual(groups["AU-EXTENDED-SEPARATE"], ["PUB-EXTENDED"])
        self.assertEqual(groups["AU-DIVISIONAL-SEPARATE"], ["PUB-DIVISIONAL"])
        self.assertEqual(groups["AU-PARENT-SEPARATE"], ["PUB-PARENT"])
        self.assertEqual(groups["AU-MISSING-SEPARATE"], ["PUB-MISSING"])

    def test_terminal_fixture_is_a_strict_three_way_partition(self) -> None:
        case = make_classification_terminal_case()
        analysis_units = case["analysis_units"]
        identities = [item["analysis_unit_id"] for item in analysis_units]
        self.assertEqual(len(identities), len(set(identities)))

        counts = {terminal: 0 for terminal in case["expected_counts"]}
        for item in analysis_units:
            counts[item["expected_terminal"]] += 1
        self.assertEqual(counts, case["expected_counts"])
        self.assertEqual(sum(counts.values()), len(analysis_units))

    def test_only_unresolved_records_have_unresolved_reasons(self) -> None:
        records = make_classification_terminal_case()["analysis_units"]
        for record in records:
            with self.subTest(analysis_unit_id=record["analysis_unit_id"]):
                if record["expected_terminal"] == "UNRESOLVED":
                    self.assertIn("unresolved_reason", record)
                else:
                    self.assertNotIn("unresolved_reason", record)


if __name__ == "__main__":
    unittest.main()
