from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from idea.corpus import (
    CorpusError,
    CorpusRunLink,
    CorpusVersionSource,
    CorpusVersion,
    PatentCorpusIngestService,
    PatentCorpusService,
)
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


class MemoryRunLinks:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], CorpusRunLink] = {}

    async def get(self, run_id: str, document_id: str) -> CorpusRunLink | None:
        return self.items.get((run_id, document_id))

    async def put_if_absent(self, link: CorpusRunLink) -> bool:
        key = (link.run_id, link.document_id)
        if key in self.items:
            return False
        self.items[key] = link
        return True

    async def mark_deep_reviewed(self, run_id: str, document_ids: tuple[str, ...]) -> None:
        for document_id in document_ids:
            key = (run_id, document_id)
            self.items[key] = replace(self.items[key], deep_reviewed=True)


class MemorySources:
    def __init__(self) -> None:
        self.items: dict[str, CorpusVersionSource] = {}

    async def put_if_absent(self, source: CorpusVersionSource) -> bool:
        if source.source_id in self.items:
            return False
        self.items[source.source_id] = source
        return True


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
        self.sources = MemorySources()
        self.service = PatentCorpusService(
            versions=self.repo,
            objects=FileObjectStore(Path(self.temp.name) / "objects"),
            sources=self.sources,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_ingest_is_content_addressed_and_idempotent(self) -> None:
        first = asyncio.run(self.service.ingest(document()))
        second = asyncio.run(self.service.ingest(document()))
        self.assertEqual(first.version_id, second.version_id)
        self.assertEqual(first.content_sha256, second.content_sha256)
        self.assertEqual(len(self.sources.items), 1)
        self.assertEqual(first.object_key, second.object_key)
        self.assertEqual(len(self.repo.items), 1)
        self.assertIs(asyncio.run(self.service.get_ready(first.version_id)), second)

    def test_changed_content_creates_new_version_without_overwrite(self) -> None:
        first = asyncio.run(self.service.ingest(document()))
        second = asyncio.run(self.service.ingest(document(title="A revised patent")))
        self.assertNotEqual(first.version_id, second.version_id)
        self.assertNotEqual(first.object_key, second.object_key)
        self.assertEqual(len(self.repo.items), 2)

    def test_source_metadata_does_not_change_stable_content_version(self) -> None:
        first_document = document()
        second_document = first_document.model_copy(
            update={
                "provider": "another-provider",
                "url": "https://other.example.test/patent",
                "raw_metadata": {"request_time": "later"},
            }
        )

        first = asyncio.run(self.service.ingest(first_document, document_id="doc-1"))
        second = asyncio.run(self.service.ingest(second_document, document_id="doc-1"))

        self.assertEqual(first.version_id, second.version_id)
        self.assertEqual(first.content_sha256, second.content_sha256)
        self.assertEqual(len(self.sources.items), 2)
        self.assertEqual(
            {source.version_id for source in self.sources.items.values()},
            {first.version_id},
        )
        self.assertEqual(
            {source.raw_response_hash for source in self.sources.items.values()},
            {None},
        )

    def test_structured_section_spans_are_part_of_durable_content(self) -> None:
        first = asyncio.run(self.service.ingest(document(), document_id="doc-1"))
        structured = document().model_copy(
            update={"section_spans": {"claims": [{"start": 1, "end": 2}]}}
        )
        second = asyncio.run(self.service.ingest(structured, document_id="doc-1"))

        self.assertNotEqual(first.version_id, second.version_id)
        self.assertNotEqual(first.content_sha256, second.content_sha256)

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

    def test_ingest_for_run_freezes_ready_version_idempotently(self) -> None:
        links = MemoryRunLinks()
        ingest = PatentCorpusIngestService(corpus=self.service, run_links=links)

        first = asyncio.run(
            ingest.ingest_many(
                run_id="run-1",
                documents=(document(),),
                document_ids={"CN 123/456": "doc-1"},
            )
        )
        second = asyncio.run(
            ingest.ingest_many(
                run_id="run-1",
                documents=(document(),),
                document_ids={"CN 123/456": "doc-1"},
            )
        )

        self.assertEqual(first, second)
        self.assertEqual(len(links.items), 1)
        link = links.items[("run-1", "doc-1")]
        self.assertEqual(link.version_id, first.version_ids[0])
        self.assertEqual(link.corpus_availability, "READY")
        self.assertFalse(link.deep_reviewed)
        self.assertEqual(first.snapshot_hash, second.snapshot_hash)

        asyncio.run(ingest.mark_deep_reviewed("run-1", ("doc-1",)))
        self.assertTrue(links.items[("run-1", "doc-1")].deep_reviewed)

    def test_ingest_for_run_rejects_conflicting_frozen_version(self) -> None:
        links = MemoryRunLinks()
        ingest = PatentCorpusIngestService(corpus=self.service, run_links=links)
        asyncio.run(
            ingest.ingest_many(
                run_id="run-1",
                documents=(document(),),
                document_ids={"CN 123/456": "doc-1"},
            )
        )

        with self.assertRaisesRegex(CorpusError, "different corpus version"):
            asyncio.run(
                ingest.ingest_many(
                    run_id="run-1",
                    documents=(document(title="changed"),),
                    document_ids={"CN 123/456": "doc-1"},
                )
            )


if __name__ == "__main__":
    unittest.main()
