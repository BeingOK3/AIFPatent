from __future__ import annotations

import unittest
from datetime import datetime, timezone

from idea.chunks import PatentChunker
from idea.context import ContextAssembler
from idea.context_adapter import LangChainAdapterUnavailable, to_langchain_messages, to_message_dicts
from idea.corpus import CorpusVersion
from idea.providers import FetchedDocument


class ContextAdapterTests(unittest.TestCase):
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
            claims_text="1. A sensor system.",
        )
        chunks = PatentChunker().chunk(version, document)
        self.context = ContextAssembler().assemble(
            purpose="FOLLOWUP",
            run_id="run-1",
            corpus_snapshot_hash="b" * 64,
            chunks=chunks,
            system_prompt="Answer with citations.",
            question="What is claimed?",
            prompt_version="prompt-v1",
            retriever_version="retriever-v1",
            input_budget=50,
            reserved_output_tokens=10,
        )

    def test_dict_adapter_is_lossless(self) -> None:
        messages = to_message_dicts(self.context)
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[0]["content"], self.context.messages[0].content)
        self.assertEqual(messages[1]["content"], self.context.messages[1].content)

    def test_langchain_dependency_is_lazy_and_explicit(self) -> None:
        try:
            messages = to_langchain_messages(self.context)
        except LangChainAdapterUnavailable:
            return
        self.assertEqual(len(messages), len(self.context.messages))
        self.assertEqual(messages[0].content, self.context.messages[0].content)


if __name__ == "__main__":
    unittest.main()
