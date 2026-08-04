from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace

from idea.providers.base import SearchHit
from landscape.publication_freeze import PublicationFreezeError, freeze_publications


def hit(number=None, url="", rank=1):
    return SearchHit(provider="fixture", provider_rank=rank, publication_number=number, url=url, title="title", publication_date="2024-01-02")


class LandscapePublicationFreezeTests(unittest.TestCase):
    def test_exact_publication_duplicates_are_deduplicated_without_losing_sources(self):
        result = freeze_publications("run", (("q2", hit("us 123 a1", rank=2)), ("q1", hit("US123A1"))))
        self.assertEqual(result.publication_count, 1)
        self.assertEqual(result.publications[0].source_queries, ("q1", "q2"))
        self.assertEqual(result.analysis_unit_count, 1)

    def test_url_is_fallback_identity_and_output_is_stable(self):
        values = (("q", hit(None, "https://example.test/patent/1")),)
        first = freeze_publications("run", values)
        second = freeze_publications("run", tuple(reversed(values)))
        self.assertEqual(first, second)

    def test_freeze_caps_publications_deterministically_and_records_truncation(self):
        values = tuple(
            ("q", hit(f"US{i}A1").model_copy(update={"publication_date": f"2024-01-{i:02d}"}))
            for i in range(1, 6)
        )
        result = freeze_publications(
            "run",
            values,
            publication_start=date(2024, 1, 1),
            publication_end=date(2024, 1, 31),
            max_publications=3,
        )
        self.assertEqual(result.publication_count, 3)
        self.assertEqual(result.truncated_count, 2)
        self.assertEqual(
            [item.publication_number for item in result.publications],
            ["US1A1", "US2A1", "US3A1"],
        )

    def test_missing_identity_fails_closed(self):
        with self.assertRaises(PublicationFreezeError):
            freeze_publications("run", (("q", SimpleNamespace(publication_number=None, url="")),))

    def test_freeze_preserves_explicit_family_and_organization_inputs(self):
        value = SearchHit(
            provider="fixture",
            provider_rank=1,
            publication_number="CN 123 A",
            application_number="CN 2024 001",
            title="title",
            snippet="abstract-like search snippet",
            priority_date="2022-01-02",
            filing_date="2023-02-03",
            publication_date="2024-03-04",
            assignee="示例公司",
        )
        publication = freeze_publications("run", (("q", value),)).publications[0]
        self.assertEqual(publication.application_number, "CN2024001")
        self.assertEqual(publication.assignee, "示例公司")
        self.assertEqual(publication.snippet, "abstract-like search snippet")
        self.assertEqual(publication.priority_date.isoformat(), "2022-01-02")

    def test_known_publication_dates_are_filtered_by_inclusive_window(self):
        values = (
            ("q", hit("US1A1").model_copy(update={"publication_date": "2023-12-31"})),
            ("q", hit("US2A1").model_copy(update={"publication_date": "2024-01-01"})),
            ("q", hit("US3A1").model_copy(update={"publication_date": "2024-03-31"})),
            ("q", hit("US4A1").model_copy(update={"publication_date": "2024-04-01"})),
        )
        result = freeze_publications(
            "run",
            values,
            publication_start=date(2024, 1, 1),
            publication_end=date(2024, 3, 31),
        )
        self.assertEqual(
            [item.publication_number for item in result.publications],
            ["US2A1", "US3A1"],
        )
        self.assertEqual(result.date_excluded_count, 2)


if __name__ == "__main__": unittest.main()
