from __future__ import annotations

import unittest

from backend.tests.landscape_v4_fixture_factory import make_family_case
from idea.providers.base import SearchHit
from landscape.family_resolution import (
    BibliographicPublication,
    FamilyResolutionError,
    resolve_families,
    resolve_frozen_publications,
)
from landscape.publication_freeze import freeze_publications


class LandscapeFamilyResolutionTests(unittest.TestCase):
    def test_fixture_matches_conservative_expected_partition(self):
        case = make_family_case()
        inputs = tuple(
            BibliographicPublication(
                publication_id=item["publication_id"],
                publication_number=item["publication_number"],
                application_number=item.get("application_number"),
                priority_numbers=tuple(item.get("priority_numbers", ())),
                relationship=item.get("relationship"),
                related_application=item.get("related_application"),
            )
            for item in case["publications"]
        )
        resolution = resolve_families(inputs)
        actual = {frozenset(unit.member_publication_ids) for unit in resolution.analysis_units}
        expected = {frozenset(members) for members in case["expected_analysis_units"].values()}
        self.assertEqual(actual, expected)
        self.assertEqual(resolution.publication_count, len(inputs))
        self.assertEqual(resolution.analysis_unit_count, 6)

    def test_provider_family_hint_alone_never_merges(self):
        values = (
            BibliographicPublication(publication_id="p1", publication_number="US1A1", family_id="same"),
            BibliographicPublication(publication_id="p2", publication_number="EP1A1", family_id="same"),
        )
        self.assertEqual(resolve_families(values).analysis_unit_count, 2)

    def test_duplicate_publications_fail_closed(self):
        value = BibliographicPublication(publication_id="p1", publication_number="US1A1")
        with self.assertRaises(FamilyResolutionError): resolve_families((value, value))

    def test_frozen_application_number_merges_but_priority_date_or_family_hint_does_not(self):
        frozen = freeze_publications(
            "run",
            tuple(
                (
                    "q",
                    SearchHit(
                        provider="fixture",
                        provider_rank=index,
                        publication_number=number,
                        application_number=application,
                        priority_date="2020-01-01",
                        family_id="provider-same",
                    ),
                )
                for index, (number, application) in enumerate(
                    (("US1A1", "APP-1"), ("US1B2", "APP-1"), ("EP2A1", None)),
                    start=1,
                )
            ),
        )
        resolution = resolve_frozen_publications(frozen)
        self.assertEqual(resolution.analysis_unit_count, 2)
        self.assertEqual(
            sorted(len(unit.member_publication_ids) for unit in resolution.analysis_units),
            [1, 2],
        )


if __name__ == "__main__": unittest.main()
