from __future__ import annotations

import asyncio
import unittest

from idea.providers.base import FetchedDocument, SearchHit, SearchProvider
from landscape.patent_snapshot import PatentSnapshotSet, PatentSnapshotStatus
from landscape.patent_snapshot_fetch import PatentSnapshotFetchService
from landscape.publication_freeze import freeze_publications


class FakeRepository:
    def __init__(self):
        self.values = {}

    def list(self, run_id):
        return tuple(sorted(self.values.values(), key=lambda item: item.publication_id))

    def put_one(self, run_id, snapshot, *, sort_order):
        existing = self.values.setdefault(snapshot.publication_id, snapshot)
        if existing != snapshot:
            raise ValueError("immutable")
        return existing

    def get(self, run_id):
        return PatentSnapshotSet(
            run_id=run_id,
            snapshots=tuple(sorted(self.values.values(), key=lambda item: item.publication_id)),
        )


class FakeProvider(SearchProvider):
    name = "fake-details"

    def __init__(self, *, failing=()):
        self.failing = set(failing)
        self.calls = []
        self.active = 0
        self.peak = 0

    async def search(self, query):
        return []

    async def fetch(self, request):
        self.calls.append(request)
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(0.005)
        self.active -= 1
        if request.publication_number in self.failing:
            raise TimeoutError("secret must not be persisted")
        return FetchedDocument(
            provider=self.name,
            publication_number=request.publication_number,
            application_number=f"APP-{request.publication_number}",
            assignees=[f"Owner {request.publication_number}"],
            url=request.url,
            abstract_text="A stable technical abstract for classification.",
        )


def _frozen(count=5):
    return freeze_publications(
        "RUN-FETCH",
        tuple(
            (
                "Q-1",
                SearchHit(
                    provider="search",
                    provider_rank=index,
                    publication_number=f"US{index}A1",
                    url=f"https://example.test/US{index}A1",
                ),
            )
            for index in range(1, count + 1)
        ),
    )


class PatentSnapshotFetchServiceTests(unittest.TestCase):
    def test_fetches_with_bounded_concurrency_and_abstract_only_requests(self):
        provider = FakeProvider()
        repository = FakeRepository()
        progress = []
        service = PatentSnapshotFetchService(
            (provider,),
            repository,
            max_concurrency=2,
            max_attempts_per_provider=1,
            retry_delay_seconds=0,
        )
        result = asyncio.run(
            service.fetch(_frozen(), progress=lambda completed, total: progress.append((completed, total)))
        )
        self.assertEqual(len(result.snapshots), 5)
        self.assertEqual(provider.peak, 2)
        self.assertTrue(all(not call.include_description for call in provider.calls))
        self.assertEqual(progress[0], (0, 5))
        self.assertEqual(progress[-1], (5, 5))

    def test_resume_skips_individually_checkpointed_snapshots(self):
        frozen = _frozen(3)
        provider = FakeProvider()
        repository = FakeRepository()
        first_service = PatentSnapshotFetchService(
            (provider,), repository, max_attempts_per_provider=1, retry_delay_seconds=0
        )
        asyncio.run(first_service.fetch(frozen))
        provider.calls.clear()
        result = asyncio.run(first_service.fetch(frozen))
        self.assertEqual(len(result.snapshots), 3)
        self.assertEqual(provider.calls, [])

    def test_exhausted_retries_become_terminal_failure_without_error_text(self):
        provider = FakeProvider(failing={"US1A1"})
        repository = FakeRepository()
        service = PatentSnapshotFetchService(
            (provider,), repository, max_attempts_per_provider=2, retry_delay_seconds=0
        )
        result = asyncio.run(service.fetch(_frozen(1)))
        snapshot = result.snapshots[0]
        self.assertEqual(snapshot.status, PatentSnapshotStatus.PROVIDER_FAILED)
        self.assertEqual(len(provider.calls), 2)
        self.assertIn("TimeoutError", snapshot.failure_reason)
        self.assertNotIn("secret", snapshot.failure_reason)


if __name__ == "__main__":
    unittest.main()
