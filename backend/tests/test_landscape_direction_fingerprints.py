from __future__ import annotations

import hashlib
import unittest

from idea.merge import MergedHit
from idea.providers.base import FetchedDocument
from landscape.direction_fingerprints import build_direction_fingerprint


class LandscapeDirectionFingerprintTests(unittest.TestCase):
    def hit(self) -> MergedHit:
        return MergedHit(
            merge_key="US123A1",
            publication_number="US123A1",
            title="GPU high-speed interconnect",
            snippet="A system for accelerator memory traffic.",
            publication_date="2026-06-01",
            assignee="NVIDIA Corporation",
            found_by=["fixture"],
            query_ids=["Q-1"],
            sources=[],
        )

    def test_search_hit_fingerprint_is_small_and_deterministic(self) -> None:
        first = build_direction_fingerprint(
            self.hit(),
            company_id="CO-NVIDIA",
        )
        second = build_direction_fingerprint(
            self.hit(),
            company_id="CO-NVIDIA",
        )
        self.assertEqual(first, second)
        self.assertEqual(first.source_kind, "SEARCH_HIT")
        self.assertLessEqual(sum(len(item.text) for item in first.evidence), 3_000)
        self.assertIn("GPU", first.technical_keywords)

    def test_run_namespace_keeps_persisted_evidence_ids_distinct(self) -> None:
        first = build_direction_fingerprint(
            self.hit(),
            company_id="CO-NVIDIA",
            evidence_namespace="run-1",
        )
        replay = build_direction_fingerprint(
            self.hit(),
            company_id="CO-NVIDIA",
            evidence_namespace="run-1",
        )
        second = build_direction_fingerprint(
            self.hit(),
            company_id="CO-NVIDIA",
            evidence_namespace="run-2",
        )

        self.assertEqual(first, replay)
        self.assertNotEqual(
            [item.evidence_id for item in first.evidence],
            [item.evidence_id for item in second.evidence],
        )

    def test_truncated_evidence_hash_matches_validated_text(self) -> None:
        document = FetchedDocument(
            provider="fixture",
            publication_number="US123A1",
            title="GPU high-speed interconnect",
            publication_date="2026-06-01",
            url="https://example.test/US123A1",
            # The 2,000-character cutoff lands on whitespace.  Pydantic
            # strips it, so hashing before the final trim would be invalid.
            abstract_text=("x" * 1_999) + " " + ("tail" * 100),
        )

        result = build_direction_fingerprint(
            self.hit(),
            company_id="CO-NVIDIA",
            document=document,
            max_text_per_section=2_000,
        )

        self.assertTrue(
            all(
                hashlib.sha256(item.text.encode("utf-8")).hexdigest()
                == item.content_hash
                for item in result.evidence
            )
        )

    def test_fetched_fingerprint_uses_abstract_and_claim_but_not_full_document(self) -> None:
        document = FetchedDocument(
            provider="fixture",
            publication_number="US123A1",
            title="GPU high-speed interconnect",
            publication_date="2026-06-01",
            url="https://example.test/US123A1",
            abstract_text="A bounded abstract about accelerator memory traffic.",
            claims_text="1. A system comprising an accelerator and an interconnect.",
            description_text="x" * 20_000,
        )
        result = build_direction_fingerprint(
            self.hit(),
            company_id="CO-NVIDIA",
            document=document,
        )
        self.assertEqual(result.source_kind, "FETCHED_DOCUMENT")
        self.assertLessEqual(
            sum(len(item.text) for item in result.evidence),
            6_000,
        )
        self.assertEqual(result.evidence[1].section_type, "ABSTRACT")


if __name__ == "__main__":
    unittest.main()
