from __future__ import annotations

import unittest
from datetime import datetime, timezone

from idea.chunks import PatentChunker
from idea.corpus import CorpusVersion
from idea.providers import FetchedDocument


class PatentChunkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.version = CorpusVersion(
            version_id="cv-fixture",
            publication_number="CN123",
            language="en",
            provider="fixture",
            object_key="patent-corpus/CN123/hash.json",
            content_sha256="a" * 64,
            normalized_size=10,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        self.document = FetchedDocument(
            provider="fixture",
            publication_number="CN123",
            url="https://example.test/CN123",
            abstract_text="An abstract.",
            claims_text="1. A sensor system.\n2. The system of claim 1, wherein it filters noise.",
            description_text="First paragraph.\n\nSecond paragraph.",
        )

    def test_chunks_preserve_structure_and_parent_claim(self) -> None:
        chunks = PatentChunker().chunk(self.version, self.document)
        self.assertEqual([chunk.section_type for chunk in chunks], ["abstract", "claims", "claims", "description", "description"])
        self.assertEqual(chunks[1].claim_kind, "independent")
        self.assertEqual(chunks[2].parent_claim_numbers, (1,))
        self.assertEqual(chunks[3].section_label, "paragraph-1")
        self.assertEqual(chunks[3].text, "First paragraph.")

    def test_chunk_ids_are_deterministic_and_version_scoped(self) -> None:
        chunker = PatentChunker()
        first = chunker.chunk(self.version, self.document)
        second = chunker.chunk(self.version, self.document)
        self.assertEqual(first, second)
        other_version = self.version.__class__(**{**self.version.__dict__, "version_id": "cv-other"})
        self.assertNotEqual(first[0].chunk_id, chunker.chunk(other_version, self.document)[0].chunk_id)

    def test_wrong_version_identity_is_rejected(self) -> None:
        wrong = self.document.model_copy(update={"publication_number": "CN999"})
        with self.assertRaises(ValueError):
            PatentChunker().chunk(self.version, wrong)


if __name__ == "__main__":
    unittest.main()
