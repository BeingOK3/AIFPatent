from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from idea.corpus import CorpusError, CorpusVersion, PatentCorpusService
from idea.object_store import FileObjectStore
from idea.providers import FetchedDocument


class MemoryRepository:
    def __init__(self) -> None:
        self.items: dict[str, CorpusVersion] = {}

    async def get(self, key: str) -> CorpusVersion | None:
        return self.items.get(key)

    async def put_if_absent(self, key: str, entity: CorpusVersion) -> bool:
        if key in self.items:
            return False
        self.items[key] = entity
        return True

    async def healthcheck(self):
        return {"ok": True}


def document(*, title: str = "A patent") -> FetchedDocument:
    return FetchedDocument(
        provider="fixture",
        publication_number="CN 123/456",
        language="en",
        url="https://example.test/patent",
        title=title,
        abstract_text="A normalized abstract.",
    )


class PatentCorpusServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = MemoryRepository()
        self.service = PatentCorpusService(
            versions=self.repo,
            objects=FileObjectStore(Path(self.temp.name) / "objects"),
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_ingest_is_content_addressed_and_idempotent(self) -> None:
        first = asyncio.run(self.service.ingest(document()))
        second = asyncio.run(self.service.ingest(document()))
        self.assertEqual(first.version_id, second.version_id)
        self.assertEqual(first.content_sha256, second.content_sha256)
        self.assertEqual(first.object_key, second.object_key)
        self.assertEqual(len(self.repo.items), 1)
        self.assertIs(asyncio.run(self.service.get_ready(first.version_id)), second)

    def test_changed_content_creates_new_version_without_overwrite(self) -> None:
        first = asyncio.run(self.service.ingest(document()))
        second = asyncio.run(self.service.ingest(document(title="A revised patent")))
        self.assertNotEqual(first.version_id, second.version_id)
        self.assertNotEqual(first.object_key, second.object_key)
        self.assertEqual(len(self.repo.items), 2)

    def test_snapshot_is_order_independent_and_detects_missing_blob(self) -> None:
        first = asyncio.run(self.service.ingest(document()))
        second = asyncio.run(self.service.ingest(document(title="Another patent")))
        left = asyncio.run(self.service.snapshot_hash((first.version_id, second.version_id)))
        right = asyncio.run(self.service.snapshot_hash((second.version_id, first.version_id)))
        self.assertEqual(left, right)
        Path(self.temp.name, "objects", first.object_key).unlink()
        with self.assertRaises(CorpusError):
            asyncio.run(self.service.snapshot_hash((first.version_id,)))

    def test_non_ready_version_is_not_usable(self) -> None:
        first = asyncio.run(self.service.ingest(document()))
        self.repo.items[first.version_id] = replace(first, status="CORRUPT")
        with self.assertRaises(CorpusError):
            asyncio.run(self.service.get_ready(first.version_id))


if __name__ == "__main__":
    unittest.main()
