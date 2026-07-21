from __future__ import annotations

import asyncio
import hashlib
import unittest

from idea.chunks import PatentChunk
from idea.config import load_config
from idea.hybrid import (
    HybridRetrievalError,
    HybridRetriever,
    HybridSearchRequest,
    QuestionType,
    RetrievalMode,
)
from idea.lexical import LexicalHit
from idea.vector import VectorHit


def chunk(
    chunk_id: str,
    *,
    version_id: str = "cv-1",
    section_type: str = "description",
    section_label: str | None = None,
    text: str | None = None,
    claim_number: int | None = None,
    claim_kind: str | None = None,
) -> PatentChunk:
    value = text or f"text for {chunk_id}"
    return PatentChunk(
        chunk_id=chunk_id,
        version_id=version_id,
        publication_number="CN1A" if version_id == "cv-1" else "CN2A",
        section_type=section_type,
        section_label=section_label or chunk_id,
        claim_number=claim_number,
        claim_kind=claim_kind,
        parent_claim_numbers=(),
        start_offset=0,
        end_offset=len(value),
        text=value,
        text_hash=hashlib.sha256(value.encode()).hexdigest(),
        token_count=len(value.split()),
        chunker_version="v1",
    )


class FakeLexical:
    def __init__(self, hits):
        self.hits = hits
        self.requests = []

    async def search(self, request):
        self.requests.append(request)
        return self.hits


class FakeVector:
    def __init__(self, hits):
        self.hits = hits
        self.requests = []

    async def search(self, request):
        self.requests.append(request)
        return self.hits


class HybridRetrieverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = load_config().rag.hybrid

    def request(self, **updates):
        values = {
            "query_id": "hq-1",
            "text": "cache eviction",
            "allowed_version_ids": ("cv-1", "cv-2"),
            "semantic_embedding": (0.6, 0.8),
            "embedding_profile_id": "ep-test",
        }
        values.update(updates)
        return HybridSearchRequest(**values)

    def test_rrf_rewards_chunks_found_by_both_retrievers(self) -> None:
        lexical_only = chunk("lexical-only")
        shared = chunk("shared")
        vector_only = chunk("vector-only")
        lexical = FakeLexical(
            (
                LexicalHit("hq-1", 1, 0.9, "fts", lexical_only),
                LexicalHit("hq-1", 3, 0.5, "fts", shared),
            )
        )
        vector = FakeVector(
            (
                VectorHit("hq-1", 1, 0.1, "exact", vector_only),
                VectorHit("hq-1", 2, 0.2, "exact", shared),
            )
        )

        result = asyncio.run(HybridRetriever(lexical, self.settings, vector=vector).search(self.request()))

        self.assertEqual(result.mode, RetrievalMode.HYBRID)
        self.assertEqual(result.hits[0].chunk.chunk_id, "shared")
        self.assertEqual(result.hits[0].sources, ("lexical", "vector"))
        self.assertEqual(result.limitations, ())
        self.assertEqual(lexical.requests[0].allowed_version_ids, ("cv-1", "cv-2"))
        self.assertEqual(vector.requests[0].allowed_version_ids, ("cv-1", "cv-2"))

    def test_claim_question_applies_explainable_section_weight(self) -> None:
        abstract = chunk("abstract", section_type="abstract")
        claim = chunk(
            "claim", section_type="claims", claim_number=1, claim_kind="independent"
        )
        lexical = FakeLexical(
            (
                LexicalHit("hq-1", 1, 1.0, "fts", abstract),
                LexicalHit("hq-1", 2, 0.9, "fts", claim),
            )
        )
        request = self.request(
            semantic_embedding=None,
            embedding_profile_id=None,
            question_type=QuestionType.CLAIM_OVERLAP,
        )

        result = asyncio.run(HybridRetriever(lexical, self.settings).search(request))

        self.assertEqual(result.hits[0].chunk.chunk_id, "claim")
        self.assertEqual(result.hits[0].section_weight, 1.30)
        self.assertEqual(result.mode, RetrievalMode.LEXICAL_ONLY)
        self.assertEqual(result.limitations, ("LEXICAL_ONLY",))

    def test_exact_text_dedup_and_per_version_diversity(self) -> None:
        duplicate_text = "same patent paragraph"
        a1 = chunk("a1", text=duplicate_text)
        a2 = chunk("a2", text=duplicate_text)
        a3 = chunk("a3")
        b1 = chunk("b1", version_id="cv-2")
        lexical = FakeLexical(
            tuple(
                LexicalHit("hq-1", rank, 1.0 / rank, "fts", item)
                for rank, item in enumerate((a1, a2, a3, b1), start=1)
            )
        )
        constrained = self.settings.model_copy(
            update={"final_limit": 2, "max_chunks_per_version": 2}
        )
        request = self.request(
            semantic_embedding=None,
            embedding_profile_id=None,
            ensure_version_diversity=True,
        )

        result = asyncio.run(HybridRetriever(lexical, constrained).search(request))

        self.assertEqual(len(result.hits), 2)
        self.assertEqual({hit.chunk.version_id for hit in result.hits}, {"cv-1", "cv-2"})
        self.assertEqual(sum(hit.chunk.text == duplicate_text for hit in result.hits), 1)

    def test_section_label_and_version_caps_prevent_one_source_domination(self) -> None:
        hits = tuple(
            LexicalHit(
                "hq-1",
                rank,
                1.0 / rank,
                "fts",
                chunk(
                    f"c{rank}",
                    section_label="long-claim" if rank <= 3 else f"p-{rank}",
                ),
            )
            for rank in range(1, 7)
        )
        settings = self.settings.model_copy(
            update={
                "final_limit": 6,
                "max_chunks_per_version": 3,
                "max_chunks_per_section_label": 1,
            }
        )
        request = self.request(semantic_embedding=None, embedding_profile_id=None)

        result = asyncio.run(HybridRetriever(FakeLexical(hits), settings).search(request))

        self.assertEqual(len(result.hits), 3)
        self.assertEqual(sum(hit.chunk.section_label == "long-claim" for hit in result.hits), 1)

    def test_scope_escape_or_conflicting_chunk_content_fails_closed(self) -> None:
        escaped = chunk("escaped", version_id="cv-outside")
        request = self.request(semantic_embedding=None, embedding_profile_id=None)
        with self.assertRaisesRegex(HybridRetrievalError, "escaped"):
            asyncio.run(
                HybridRetriever(
                    FakeLexical((LexicalHit("hq-1", 1, 1.0, "fts", escaped),)),
                    self.settings,
                ).search(request)
            )

        left = chunk("same", text="left")
        right = chunk("same", text="right")
        with self.assertRaisesRegex(HybridRetrievalError, "conflicting"):
            asyncio.run(
                HybridRetriever(
                    FakeLexical((LexicalHit("hq-1", 1, 1.0, "fts", left),)),
                    self.settings,
                    vector=FakeVector((VectorHit("hq-1", 1, 0.1, "exact", right),)),
                ).search(self.request())
            )

    def test_semantic_coordinates_are_atomic(self) -> None:
        with self.assertRaisesRegex(ValueError, "provided together"):
            self.request(embedding_profile_id=None)


if __name__ == "__main__":
    unittest.main()
