from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from idea.runtime import RuntimeConfigurationError
from tools.index_embeddings import index_chunk_batches


class IndexEmbeddingToolTests(unittest.TestCase):
    def test_batches_all_chunks_then_activates_one_complete_profile_scope(self) -> None:
        class Service:
            def __init__(self):
                self.events = []

            async def index_chunks(self, chunks):
                self.events.append(("index", tuple(item.chunk_id for item in chunks)))

            async def activate_for_chunks(self, chunk_ids):
                self.events.append(("activate", tuple(chunk_ids)))

        service = Service()
        chunks = tuple(SimpleNamespace(chunk_id=f"c-{index}") for index in range(5))
        count = asyncio.run(index_chunk_batches(service, chunks, batch_size=2))
        self.assertEqual(count, 5)
        self.assertEqual(service.events, [
            ("index", ("c-0", "c-1")),
            ("index", ("c-2", "c-3")),
            ("index", ("c-4",)),
            ("activate", ("c-0", "c-1", "c-2", "c-3", "c-4")),
        ])

    def test_empty_duplicate_or_invalid_batch_fails_before_activation(self) -> None:
        class Service:
            async def index_chunks(self, chunks):
                raise AssertionError("invalid input must not be indexed")

            async def activate_for_chunks(self, chunk_ids):
                raise AssertionError("invalid input must not activate")

        with self.assertRaisesRegex(RuntimeConfigurationError, "at least one"):
            asyncio.run(index_chunk_batches(Service(), ()))
        duplicate = (
            SimpleNamespace(chunk_id="same"), SimpleNamespace(chunk_id="same")
        )
        with self.assertRaisesRegex(RuntimeConfigurationError, "unique"):
            asyncio.run(index_chunk_batches(Service(), duplicate))
        with self.assertRaisesRegex(RuntimeConfigurationError, "batch size"):
            asyncio.run(index_chunk_batches(
                Service(), (SimpleNamespace(chunk_id="one"),), batch_size=0
            ))


if __name__ == "__main__":
    unittest.main()
