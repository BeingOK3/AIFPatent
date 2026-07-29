from __future__ import annotations

import unittest

from landscape.deep_selection import select_deep_patents
from landscape.schemas import LandscapeDirectionEvidence, LandscapeDirectionFingerprint


def fingerprint(publication: str, company: str, keyword: str, published: str):
    return LandscapeDirectionFingerprint(
        publication_number=publication,
        company_id=company,
        title=keyword,
        publication_date=published,
        source_kind="SEARCH_HIT",
        technical_keywords=[keyword],
        evidence=[
            LandscapeDirectionEvidence(
                evidence_id=f"EV-DIR-{publication}",
                section_type="SNIPPET",
                text=keyword,
                content_hash="a" * 64,
            )
        ],
    )


class LandscapeDeepSelectionTests(unittest.TestCase):
    def test_selection_covers_company_direction_and_time_before_score_fill(self):
        values = {
            "P1": fingerprint("P1", "CO-A", "加速器", "2026-01-01"),
            "P2": fingerprint("P2", "CO-A", "互连", "2026-04-01"),
            "P3": fingerprint("P3", "CO-B", "加速器", "2026-04-02"),
        }
        selected = select_deep_patents(values, limit=3)
        self.assertEqual({item.company_id for item in selected}, {"CO-A", "CO-B"})
        self.assertEqual({item.time_bucket for item in selected}, {"2026-Q1", "2026-Q2"})
        self.assertTrue(
            any("COMPANY_COVERAGE" in item.reason_codes for item in selected)
        )


if __name__ == "__main__":
    unittest.main()
