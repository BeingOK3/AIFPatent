from __future__ import annotations

import unittest

from idea.merge import merge_hits, normalize_application_number, normalize_publication_number
from idea.providers import SearchHit


def hit(provider, rank, publication=None, application=None, family=None, **kwargs):
    return SearchHit(
        provider=provider,
        provider_rank=rank,
        publication_number=publication,
        application_number=application,
        family_id=family,
        url=kwargs.pop("url", f"https://example.test/{provider}/{rank}"),
        **kwargs,
    )


class MergeTests(unittest.TestCase):
    def test_identifier_normalization_removes_formatting(self) -> None:
        self.assertEqual(normalize_publication_number("US 10,893,120 B2"), "US10893120B2")
        self.assertEqual(normalize_application_number("US 15/999,001"), "US15999001")

    def test_same_publication_from_two_providers_is_one_result_with_provenance(self) -> None:
        merged = merge_hits(
            [
                ("Q1", [hit("exa_mcp", 1, "US 10,893,120 B2", title="Short")]),
                (
                    "Q2",
                    [hit(
                        "google_patents_local",
                        3,
                        "US10893120B2",
                        title="Data caching and data-aware placement",
                        snippet="Detailed abstract",
                    )],
                ),
            ]
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].publication_number, "US10893120B2")
        self.assertEqual(merged[0].found_by, ["exa_mcp", "google_patents_local"])
        self.assertEqual(merged[0].query_ids, ["Q1", "Q2"])
        self.assertEqual(merged[0].title, "Data caching and data-aware placement")
        self.assertEqual(len(merged[0].sources), 2)

    def test_publication_versions_merge_when_application_number_matches(self) -> None:
        merged = merge_hits(
            [
                ("Q1", [hit("exa_mcp", 1, "US2020000001A1", "US16/123,456")]),
                ("Q2", [hit("google_patents_local", 1, "US11111111B2", "US16123456")]),
            ]
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].application_number, "US16123456")

    def test_known_family_id_merges_different_jurisdictions(self) -> None:
        merged = merge_hits(
            [
                ("Q1", [hit("exa_mcp", 1, "US1A1", family="fam-42")]),
                ("Q1", [hit("google_patents_local", 2, "EP1A1", family="FAM42")]),
            ]
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].family_id, "FAM42")

    def test_title_date_assignee_similarity_only_marks_possible_family(self) -> None:
        common = {
            "title": "Cache affinity based scheduling",
            "priority_date": "1998-06-17",
            "assignee": "IBM",
        }
        merged = merge_hits(
            [
                ("Q1", [hit("exa_mcp", 1, "US1A1", **common)]),
                ("Q2", [hit("google_patents_local", 1, "EP1A1", **common)]),
            ]
        )
        self.assertEqual(len(merged), 2)
        self.assertTrue(merged[0].possible_family_keys)
        self.assertEqual(merged[0].possible_family_keys, merged[1].possible_family_keys)

    def test_same_provider_duplicate_is_deduplicated_without_losing_queries(self) -> None:
        merged = merge_hits(
            [
                ("Q1", [hit("exa_mcp", 1, "EP1A1")]),
                ("Q2", [hit("exa_mcp", 4, "EP 1 A1")]),
            ]
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].query_ids, ["Q1", "Q2"])
        self.assertEqual(len(merged[0].sources), 2)


if __name__ == "__main__":
    unittest.main()
