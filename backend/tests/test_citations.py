from __future__ import annotations

import hashlib
import unittest

from idea.citations import CitationVerificationError, CitationVerifier


def binding():
    text = "1. A cache controller using a heat score."
    return {
        "context_id": "CTX-123",
        "feature_id": "F1",
        "alias": "C2",
        "chunk_id": "chunk-1",
        "version_id": "cv-1",
        "publication_number": "US123456A1",
        "section_type": "claims",
        "section_label": "claim-1",
        "claim_number": 1,
        "start_offset": 0,
        "end_offset": len(text),
        "text_hash": hashlib.sha256(text.encode()).hexdigest(),
        "excerpt": text,
    }


def chunk_row():
    value = binding()
    return {key: value[key] for key in (
        "chunk_id", "version_id", "publication_number", "section_type",
        "section_label", "claim_number", "start_offset", "end_offset",
        "text_hash",
    )} | {"text": value["excerpt"]}


class CitationVerifierTests(unittest.TestCase):
    def test_exact_binding_becomes_verified_citation(self) -> None:
        citation = CitationVerifier.verify(binding(), chunk_row())
        self.assertEqual(citation.alias, "C2")
        self.assertEqual(citation.feature_id, "F1")
        self.assertEqual(citation.excerpt, chunk_row()["text"])

    def test_any_identity_or_excerpt_tampering_fails_closed(self) -> None:
        for field, value in (
            ("version_id", "cv-other"),
            ("publication_number", "US999A1"),
            ("section_label", "claim-9"),
            ("start_offset", 1),
            ("text_hash", "0" * 64),
            ("excerpt", "rewritten evidence"),
        ):
            with self.subTest(field=field):
                changed = binding()
                changed[field] = value
                with self.assertRaises(CitationVerificationError):
                    CitationVerifier.verify(changed, chunk_row())

    def test_unknown_alias_format_is_rejected(self) -> None:
        changed = binding()
        changed["alias"] = "E1"
        with self.assertRaisesRegex(CitationVerificationError, "alias"):
            CitationVerifier.verify(changed, chunk_row())


if __name__ == "__main__":
    unittest.main()
