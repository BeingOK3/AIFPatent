from __future__ import annotations

import asyncio
import math
import unittest
from unittest.mock import patch

from idea.config import load_config
from idea.embeddings import (
    CachedEmbedding,
    EmbeddingError,
    EmbeddingProfile,
    EmbeddingService,
    OpenAICompatibleEmbeddingProvider,
)


class MemoryCache:
    def __init__(self) -> None:
        self.profile = None
        self.values: dict[str, CachedEmbedding] = {}
        self.put_batches: list[tuple[CachedEmbedding, ...]] = []
        self.links = ()
        self.activated = ()

    async def ensure_profile(self, profile):
        self.profile = profile

    async def get_many(self, profile_id, text_hashes):
        return {
            key: self.values[key]
            for key in text_hashes
            if key in self.values and self.values[key].profile_id == profile_id
        }

    async def put_many(self, values):
        self.put_batches.append(values)
        self.values.update({value.text_hash: value for value in values})

    async def link_chunks(self, profile_id, chunk_text_hashes):
        self.links = (profile_id, chunk_text_hashes)

    async def activate_profile(self, profile_id, required_chunk_ids):
        self.activated = (profile_id, required_chunk_ids)


class FakeProvider:
    provider = "fixture"
    model = "multilingual-v1"
    dimensions = 2

    def __init__(self) -> None:
        self.document_calls: list[list[str]] = []

    async def embed_documents(self, texts):
        self.document_calls.append(texts)
        return [[3.0, 4.0] for _ in texts]

    async def embed_query(self, text):
        return [0.0, 2.0]


class EmbeddingTests(unittest.TestCase):
    def test_profile_id_is_stable_and_version_specific(self) -> None:
        first = EmbeddingProfile("fixture", "model-a", 2)
        same = EmbeddingProfile("fixture", "model-a", 2)
        changed = EmbeddingProfile("fixture", "model-b", 2)

        self.assertEqual(first.profile_id, same.profile_id)
        self.assertNotEqual(first.profile_id, changed.profile_id)
        self.assertTrue(first.profile_id.startswith("ep-"))

    def test_service_deduplicates_caches_batches_and_preserves_input_order(self) -> None:
        provider = FakeProvider()
        cache = MemoryCache()
        service = EmbeddingService(provider, cache, batch_size=1)

        first = asyncio.run(service.embed_documents(["alpha", "beta", "alpha"]))
        second = asyncio.run(service.embed_documents(["beta", "alpha"]))

        self.assertEqual(provider.document_calls, [["alpha"], ["beta"]])
        self.assertEqual(len(cache.values), 2)
        self.assertEqual(first[0], first[2])
        self.assertEqual(second, (first[1], first[0]))
        self.assertTrue(math.isclose(first[0].vector_norm, 1.0))
        self.assertEqual(first[0].embedding, (0.6, 0.8))

    def test_query_uses_same_profile_normalization_without_persisting(self) -> None:
        cache = MemoryCache()
        vector = asyncio.run(EmbeddingService(FakeProvider(), cache).embed_query("缓存淘汰"))

        self.assertEqual(vector, (0.0, 1.0))
        self.assertEqual(cache.values, {})

    def test_chunk_indexing_verifies_hash_links_and_activates_explicit_scope(self) -> None:
        from types import SimpleNamespace

        provider = FakeProvider()
        cache = MemoryCache()
        service = EmbeddingService(provider, cache)
        text = "cache claim"
        chunk = SimpleNamespace(
            chunk_id="chunk-1", text=text, text_hash=service.text_hash(text)
        )

        asyncio.run(service.index_chunks([chunk]))
        asyncio.run(service.activate_for_chunks(["chunk-1", "chunk-1"]))

        self.assertEqual(cache.links[0], service.profile.profile_id)
        self.assertEqual(cache.links[1], (("chunk-1", service.text_hash(text)),))
        self.assertEqual(cache.activated, (service.profile.profile_id, ("chunk-1",)))

        bad = SimpleNamespace(chunk_id="chunk-2", text=text, text_hash="a" * 64)
        with self.assertRaisesRegex(EmbeddingError, "text hash"):
            asyncio.run(service.index_chunks([bad]))

    def test_wrong_dimensions_and_non_finite_vectors_fail_closed(self) -> None:
        provider = FakeProvider()
        cache = MemoryCache()
        service = EmbeddingService(provider, cache)

        async def wrong(_texts):
            return [[1.0]]

        provider.embed_documents = wrong
        with self.assertRaisesRegex(EmbeddingError, "dimensions mismatch"):
            asyncio.run(service.embed_documents(["alpha"]))

        async def non_finite(_texts):
            return [[float("nan"), 1.0]]

        provider.embed_documents = non_finite
        with self.assertRaisesRegex(EmbeddingError, "non-finite"):
            asyncio.run(service.embed_documents(["beta"]))

    def test_non_normalized_cache_value_fails_closed(self) -> None:
        provider = FakeProvider()
        cache = MemoryCache()
        service = EmbeddingService(provider, cache)
        text_hash = service.text_hash("alpha")
        cache.values[text_hash] = CachedEmbedding(
            text_hash=text_hash,
            profile_id=service.profile.profile_id,
            embedding=(3.0, 4.0),
            vector_norm=5.0,
        )

        with self.assertRaisesRegex(EmbeddingError, "non-normalized"):
            asyncio.run(service.embed_documents(["alpha"]))

    def test_openai_compatible_adapter_orders_results_and_keeps_secret_out_of_payload(self) -> None:
        settings = load_config().embedding.model_copy(
            update={"enabled": True, "dimensions": 2}
        )
        calls = []

        async def transport(payload, headers):
            calls.append((payload, headers))
            return {
                "data": [
                    {"index": 1, "embedding": [0.0, 2.0]},
                    {"index": 0, "embedding": [3.0, 4.0]},
                ]
            }

        with patch.dict("os.environ", {settings.api_key_env: "deployment-test-secret"}):
            provider = OpenAICompatibleEmbeddingProvider(settings, transport=transport)
            values = asyncio.run(provider.embed_documents(["alpha", "beta"]))

        self.assertEqual(values, [[3.0, 4.0], [0.0, 2.0]])
        self.assertEqual(calls[0][0], {"model": settings.model, "input": ["alpha", "beta"]})
        self.assertNotIn("secret", str(calls[0][0]).lower())
        self.assertEqual(calls[0][1]["Authorization"], "Bearer deployment-test-secret")

    def test_missing_deployment_credential_fails_without_chat_byok_fallback(self) -> None:
        settings = load_config().embedding
        with patch.dict("os.environ", {settings.api_key_env: "", "LLM_API_KEY": "chat-only"}):
            with self.assertRaisesRegex(EmbeddingError, "deployment embedding credential"):
                OpenAICompatibleEmbeddingProvider(settings).api_key()


if __name__ == "__main__":
    unittest.main()
