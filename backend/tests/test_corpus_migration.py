from __future__ import annotations

import asyncio
import hashlib
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from idea.corpus import CorpusIngestResult, CorpusRunLink
from idea.corpus_migration import (
    HistoricalCorpusCandidate,
    HistoricalCorpusMigrator,
    MigrationStatus,
    RetrievalCorpusFetcher,
)
from idea.database import Database, canonical_json, now_ms
from idea.providers import FetchedDocument


class MemoryRunLinks:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], CorpusRunLink] = {}

    async def get(self, run_id: str, document_id: str):
        return self.items.get((run_id, document_id))


class FaultyRunLinks(MemoryRunLinks):
    async def get(self, run_id: str, document_id: str):
        if document_id == "doc-CN107":
            raise ConnectionError("corpus database unavailable")
        return await super().get(run_id, document_id)


class RejectingRunLinks(MemoryRunLinks):
    async def get(self, run_id: str, document_id: str):
        raise AssertionError("dry-run must not access corpus storage")


class FakeIngest:
    def __init__(self, links: MemoryRunLinks) -> None:
        self.run_links = links
        self.calls: list[tuple[str, tuple[FetchedDocument, ...], dict[str, str]]] = []
        self.mark_calls: list[tuple[str, tuple[str, ...]]] = []

    async def ingest_many(self, *, run_id, documents, document_ids):
        self.calls.append((run_id, documents, document_ids))
        return CorpusIngestResult(version_ids=("cv-fixture",), snapshot_hash="a" * 64)

    async def mark_deep_reviewed(self, run_id, document_ids):
        self.mark_calls.append((run_id, document_ids))


@dataclass
class FakeFetcher:
    document: FetchedDocument | None

    def __post_init__(self) -> None:
        self.calls = []

    async def fetch(self, candidate):
        self.calls.append(candidate)
        return self.document, (() if self.document else ("NOT_FOUND",))


class FakeRetrieval:
    def __init__(self) -> None:
        self.calls = []

    async def rehydrate_document(self, **kwargs):
        self.calls.append(kwargs)
        return None, ("provider:UNAVAILABLE",)


class HistoricalCorpusMigratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "idea.sqlite3")
        self.database.initialize()
        case = self.database.create_case("Historical corpus")
        self.run = self.database.create_run(
            case_id=case["case_id"],
            input_text="Historical IDEA input.",
            evaluation_date="2026-07-21",
            date_basis="publication",
            analysis_scope="full",
            model="fixture",
            skill_version="fixture/1",
            workflow_version="fixture/1",
            config_snapshot={},
        )
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE idea_runs SET status = 'COMPLETED' WHERE run_id = ?",
                (self.run["run_id"],),
            )
        self.links = MemoryRunLinks()
        self.ingest = FakeIngest(self.links)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def add_document(
        self,
        publication_number: str,
        *,
        abstract: str | None,
        content_hash: str | None = None,
        deep_reviewed: bool = True,
    ) -> str:
        document_id = f"doc-{publication_number}"
        claims = "1. A historical claim." if abstract is not None else None
        description = "Historical description." if abstract is not None else None
        content = "\n\n".join((abstract or "", claims or "", description or ""))
        digest = content_hash or (hashlib.sha256(content.encode("utf-8")).hexdigest())
        timestamp = now_ms()
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO patent_documents(
                    document_id, publication_number, title, language, url,
                    abstract_text, claims_text, description_text, content_hash,
                    metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, 'en', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document_id,
                    publication_number,
                    "Historical patent",
                    f"https://patents.google.com/patent/{publication_number}/en",
                    abstract,
                    claims,
                    description,
                    digest,
                    canonical_json({"source": "fixture"}),
                    timestamp,
                    timestamp,
                ),
            )
            connection.execute(
                "INSERT INTO run_documents(run_id, document_id, deep_reviewed) VALUES (?, ?, ?)",
                (self.run["run_id"], document_id, int(deep_reviewed)),
            )
        return document_id

    def migrator(self, fetcher=None) -> HistoricalCorpusMigrator:
        return HistoricalCorpusMigrator(
            database=self.database,
            ingest=self.ingest,
            fetcher=fetcher,
        )

    def test_dry_run_classifies_local_text_without_writes_or_network(self) -> None:
        self.add_document("CN100", abstract="Historical abstract.")
        fetcher = FakeFetcher(None)
        self.links = RejectingRunLinks()
        self.ingest = FakeIngest(self.links)

        report = asyncio.run(self.migrator(fetcher).migrate(apply=False))

        self.assertEqual(report.outcomes[0].status, MigrationStatus.LOCAL_READY)
        self.assertEqual(self.ingest.calls, [])
        self.assertEqual(self.ingest.mark_calls, [])
        self.assertEqual(fetcher.calls, [])

    def test_apply_ingests_valid_local_text_without_fetching(self) -> None:
        document_id = self.add_document("CN101", abstract="Historical abstract.")
        fetcher = FakeFetcher(None)

        report = asyncio.run(self.migrator(fetcher).migrate(apply=True))

        self.assertEqual(report.outcomes[0].status, MigrationStatus.MIGRATED_LOCAL)
        self.assertEqual(fetcher.calls, [])
        self.assertEqual(self.ingest.calls[0][0], self.run["run_id"])
        self.assertEqual(self.ingest.calls[0][2], {"CN101": document_id})
        self.assertEqual(
            self.ingest.mark_calls,
            [(self.run["run_id"], (document_id,))],
        )

    def test_apply_rehydrates_missing_text_through_fetcher(self) -> None:
        fetched = FetchedDocument(
            provider="fixture",
            publication_number="CN102",
            language="en",
            url="https://example.test/CN102",
            abstract_text="Rehydrated abstract.",
        )
        expected_hash = hashlib.sha256(
            "Rehydrated abstract.\n\n\n\n".encode("utf-8")
        ).hexdigest()
        document_id = self.add_document(
            "CN102", abstract=None, content_hash=expected_hash
        )
        fetcher = FakeFetcher(fetched)

        report = asyncio.run(self.migrator(fetcher).migrate(apply=True))

        self.assertEqual(report.outcomes[0].status, MigrationStatus.REHYDRATED)
        self.assertEqual(self.ingest.calls[0][2], {"CN102": document_id})
        self.assertEqual(len(fetcher.calls), 1)

    def test_rehydrated_text_must_match_historical_content_hash(self) -> None:
        self.add_document("CN109", abstract=None, content_hash="f" * 64)
        fetcher = FakeFetcher(
            FetchedDocument(
                provider="fixture",
                publication_number="CN109",
                language="en",
                url="https://example.test/CN109",
                abstract_text="Current provider content differs.",
            )
        )

        report = asyncio.run(self.migrator(fetcher).migrate(apply=True))

        self.assertEqual(report.outcomes[0].status, MigrationStatus.FAILED)
        self.assertEqual(report.outcomes[0].error_code, "CorpusMigrationError")
        self.assertEqual(self.ingest.calls, [])

    def test_missing_text_is_rehydratable_in_dry_run_and_unavailable_on_failure(self) -> None:
        self.add_document("CN103", abstract=None)
        fetcher = FakeFetcher(None)

        dry_run = asyncio.run(self.migrator(fetcher).migrate(apply=False))
        applied = asyncio.run(self.migrator(fetcher).migrate(apply=True))

        self.assertEqual(dry_run.outcomes[0].status, MigrationStatus.REHYDRATABLE)
        self.assertEqual(applied.outcomes[0].status, MigrationStatus.UNAVAILABLE)
        self.assertEqual(applied.outcomes[0].error_code, "NOT_FOUND")

    def test_ready_link_is_skipped_and_hash_corruption_fails_closed(self) -> None:
        ready_document = self.add_document("CN104", abstract="Already migrated.")
        self.links.items[(self.run["run_id"], ready_document)] = CorpusRunLink(
            run_id=self.run["run_id"],
            document_id=ready_document,
            version_id="cv-existing",
        )
        self.add_document("CN105", abstract="Corrupt text.", content_hash="f" * 64)

        report = asyncio.run(self.migrator().migrate(apply=True))

        statuses = {item.publication_number: item.status for item in report.outcomes}
        self.assertEqual(statuses["CN104"], MigrationStatus.ALREADY_READY)
        self.assertEqual(statuses["CN105"], MigrationStatus.FAILED)
        self.assertEqual(len(self.ingest.calls), 0)
        self.assertEqual(
            self.ingest.mark_calls,
            [(self.run["run_id"], (ready_document,))],
        )

    def test_retrieval_fetcher_preserves_historical_identity_fields(self) -> None:
        retrieval = FakeRetrieval()
        fetcher = RetrievalCorpusFetcher(retrieval)
        candidate = HistoricalCorpusCandidate(
            run_id="run-1",
            document_id="doc-1",
            publication_number="CN106",
            language="zh",
            url="https://patents.google.com/patent/CN106/zh",
            deep_reviewed=True,
            row={},
        )

        result = asyncio.run(fetcher.fetch(candidate))

        self.assertEqual(result, (None, ("provider:UNAVAILABLE",)))
        self.assertEqual(
            retrieval.calls,
            [
                {
                    "run_id": "run-1",
                    "publication_number": "CN106",
                    "url": "https://patents.google.com/patent/CN106/zh",
                    "language": "zh",
                }
            ],
        )

    def test_one_link_lookup_failure_does_not_abort_remaining_documents(self) -> None:
        self.add_document("CN107", abstract="First historical abstract.")
        self.add_document("CN108", abstract="Second historical abstract.")
        self.links = FaultyRunLinks()
        self.ingest = FakeIngest(self.links)

        report = asyncio.run(self.migrator().migrate(apply=True))

        statuses = {item.publication_number: item.status for item in report.outcomes}
        self.assertEqual(statuses["CN107"], MigrationStatus.FAILED)
        self.assertEqual(statuses["CN108"], MigrationStatus.MIGRATED_LOCAL)
        self.assertEqual(len(self.ingest.calls), 1)

    def test_non_deep_reviewed_documents_are_outside_migration_scope(self) -> None:
        self.add_document(
            "CN110",
            abstract="Screened but not deeply reviewed.",
            deep_reviewed=False,
        )
        fetcher = FakeFetcher(None)

        report = asyncio.run(self.migrator(fetcher).migrate(apply=True))

        self.assertEqual(report.outcomes, ())
        self.assertEqual(fetcher.calls, [])
        self.assertEqual(self.ingest.calls, [])


if __name__ == "__main__":
    unittest.main()
