from __future__ import annotations

import unittest
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

    def test_missing_identity_fails_closed(self):
        with self.assertRaises(PublicationFreezeError):
            freeze_publications("run", (("q", SimpleNamespace(publication_number=None, url="")),))


if __name__ == "__main__": unittest.main()
