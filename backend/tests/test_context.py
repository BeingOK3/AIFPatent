from __future__ import annotations

import unittest
from datetime import datetime, timezone

from idea.chunks import PatentChunker
from idea.context import ContextAssembler, ContextAssemblyError
from idea.corpus import CorpusVersion
from idea.providers import FetchedDocument


class ContextAssemblerTests(unittest.TestCase):
    def setUp(self) -> None:
        version = CorpusVersion(
            version_id="cv-fixture",
            publication_number="CN123",
            language="en",
            provider="fixture",
            object_key="corpus/CN123/hash.json",
            content_sha256="a" * 64,
            normalized_size=10,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        document = FetchedDocument(
            provider="fixture",
            publication_number="CN123",
            url="https://example.test/CN123",
            abstract_text="A short abstract.",
            claims_text="1. A sensor system.\n2. The system of claim 1, wherein it filters noise.",
        )
        self.chunks = PatentChunker().chunk(version, document)

    def test_context_hash_is_deterministic_and_citations_are_bound(self) -> None:
        kwargs = dict(
            purpose="FOLLOWUP",
            run_id="run-1",
            turn_id="turn-1",
            corpus_snapshot_hash="b" * 64,
            chunks=self.chunks,
            system_prompt="Answer with citations.",
            question="Which claim addresses noise filtering?",
            prompt_version="prompt-v1",
            retriever_version="retriever-v1",
            input_budget=100,
            reserved_output_tokens=20,
        )
        first = ContextAssembler().assemble(**kwargs)
        second = ContextAssembler().assemble(**kwargs)
        self.assertEqual(first.context_hash, second.context_hash)
        self.assertEqual(first.context_id, second.context_id)
        self.assertTrue(first.context_id.startswith("CTX-"))
        self.assertEqual([item["alias"] for item in first.selected_chunks], ["C1", "C2", "C3"])
        first_binding = first.selected_chunks[0]
        self.assertEqual(first_binding["section_type"], "abstract")
        self.assertEqual(first_binding["start_offset"], 0)
        self.assertEqual(first_binding["end_offset"], len("A short abstract."))
        self.assertEqual(first_binding["excerpt"], "A short abstract.")
        self.assertEqual(first_binding["text_hash"], self.chunks[0].text_hash)
        self.assertNotIn("api_key", first.messages[1].content.lower())

    def test_budget_exclusions_are_explicit(self) -> None:
        result = ContextAssembler().assemble(
            purpose="INITIAL_REVIEW",
            run_id="run-1",
            corpus_snapshot_hash="b" * 64,
            chunks=self.chunks,
            system_prompt="Answer.",
            question="What is relevant?",
            prompt_version="prompt-v1",
            retriever_version="retriever-v1",
            input_budget=14,
            reserved_output_tokens=10,
        )
        self.assertTrue(result.excluded_chunks)
        self.assertIn("BUDGET_EXCLUSIONS", result.limitations)

    def test_budget_that_cannot_fit_evidence_fails_closed(self) -> None:
        with self.assertRaises(ContextAssemblyError):
            ContextAssembler().assemble(
                purpose="FOLLOWUP",
                run_id="run-1",
                corpus_snapshot_hash="b" * 64,
                chunks=self.chunks,
                system_prompt="Answer.",
                question="Question.",
                prompt_version="prompt-v1",
                retriever_version="retriever-v1",
                input_budget=1,
                reserved_output_tokens=10,
            )


if __name__ == "__main__":
    unittest.main()
