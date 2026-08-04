from __future__ import annotations

import unittest

from idea.providers.base import FetchedDocument
from landscape.abstract_evidence import AbstractStatus, abstract_from_document, abstract_provider_failure


def document(abstract: str, claims: str = "SHOULD NEVER BE USED"):
    return FetchedDocument(
        provider="fixture", publication_number="US123A1", url="https://example.test/US123A1",
        title="Title", abstract_text=abstract, claims_text=claims, description_text="private description",
    )


class LandscapeAbstractEvidenceTests(unittest.TestCase):
    def test_available_abstract_creates_sentence_evidence_without_claims(self):
        value = abstract_from_document("PUB-1", document("系统读取状态数据。通过反馈调整控制参数。"))
        self.assertEqual(value.status, AbstractStatus.AVAILABLE)
        self.assertEqual(len(value.evidence_ids), 2)
        self.assertNotIn("SHOULD", value.model_dump_json())
        self.assertTrue(value.content_hash)

    def test_missing_and_insufficient_abstracts_are_distinct_unresolved_states(self):
        missing = abstract_from_document("PUB-1", document(""))
        invalid = abstract_from_document("PUB-2", document("太短"))
        self.assertEqual(missing.status, AbstractStatus.MISSING)
        self.assertEqual(invalid.status, AbstractStatus.INVALID)
        self.assertEqual(missing.evidence_ids, ())
        self.assertTrue(invalid.unresolved_reason)

    def test_provider_failure_is_explicit(self):
        value = abstract_provider_failure("PUB-1", "fixture", "TIMEOUT")
        self.assertEqual(value.status, AbstractStatus.PROVIDER_FAILED)
        self.assertEqual(value.unresolved_reason, "TIMEOUT")


if __name__ == "__main__": unittest.main()
