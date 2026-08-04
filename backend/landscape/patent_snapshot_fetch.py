from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Sequence

from idea.providers.base import FetchRequest, SearchProvider

from .patent_snapshot import (
    PatentSnapshot,
    PatentSnapshotSet,
    failed_snapshot,
    snapshot_from_document,
)
from .publication_freeze import FrozenPublication, FrozenPublicationSet


ProgressCallback = Callable[[int, int], None | Awaitable[None]]


class PatentSnapshotFetchError(RuntimeError):
    pass


class PatentSnapshotFetchService:
    def __init__(
        self,
        providers: Sequence[SearchProvider],
        repository,
        *,
        max_concurrency: int = 8,
        timeout_seconds: float = 45,
        max_attempts_per_provider: int = 3,
        retry_delay_seconds: float = 0.25,
    ):
        if not providers:
            raise ValueError("at least one patent detail provider is required")
        if not 1 <= max_concurrency <= 64:
            raise ValueError("fetch concurrency must be between 1 and 64")
        if timeout_seconds <= 0:
            raise ValueError("fetch timeout must be positive")
        if not 1 <= max_attempts_per_provider <= 10:
            raise ValueError("fetch attempts must be between 1 and 10")
        if retry_delay_seconds < 0:
            raise ValueError("retry delay cannot be negative")
        self.providers = tuple(providers)
        self.repository = repository
        self.max_concurrency = max_concurrency
        self.timeout_seconds = timeout_seconds
        self.max_attempts_per_provider = max_attempts_per_provider
        self.retry_delay_seconds = retry_delay_seconds

    async def fetch(
        self,
        frozen: FrozenPublicationSet,
        *,
        progress: ProgressCallback | None = None,
    ) -> PatentSnapshotSet:
        existing = self.repository.list(frozen.run_id)
        expected_by_id = {item.publication_id: item for item in frozen.publications}
        existing_ids = {item.publication_id for item in existing}
        if not existing_ids <= set(expected_by_id):
            raise PatentSnapshotFetchError(
                "stored snapshot exists outside the frozen publication set"
            )
        completed = len(existing)
        total = len(frozen.publications)
        if progress is not None:
            await _report(progress, completed, total)
        order_by_id = {
            item.publication_id: order
            for order, item in enumerate(
                sorted(frozen.publications, key=lambda value: value.publication_id),
                start=1,
            )
        }
        pending = [
            item for item in frozen.publications if item.publication_id not in existing_ids
        ]
        semaphore = asyncio.Semaphore(self.max_concurrency)
        progress_lock = asyncio.Lock()

        async def one(item: FrozenPublication) -> PatentSnapshot:
            nonlocal completed
            async with semaphore:
                snapshot = await self._fetch_one(item)
                stored = self.repository.put_one(
                    frozen.run_id,
                    snapshot,
                    sort_order=order_by_id[item.publication_id],
                )
            async with progress_lock:
                completed += 1
                if progress is not None:
                    await _report(progress, completed, total)
            return stored

        await asyncio.gather(*(one(item) for item in pending))
        return self.repository.get(frozen.run_id)

    async def _fetch_one(self, frozen: FrozenPublication) -> PatentSnapshot:
        error_types: list[str] = []
        for provider in self.providers:
            for attempt in range(1, self.max_attempts_per_provider + 1):
                try:
                    document = await asyncio.wait_for(
                        provider.fetch(
                            FetchRequest(
                                request_id=f"SNAP-{frozen.publication_id}-{provider.name}-{attempt}",
                                publication_number=frozen.publication_number,
                                url=frozen.url,
                                include_description=False,
                            )
                        ),
                        timeout=self.timeout_seconds,
                    )
                    return snapshot_from_document(frozen, document)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    error_types.append(type(exc).__name__)
                    if attempt < self.max_attempts_per_provider and self.retry_delay_seconds:
                        await asyncio.sleep(
                            min(self.retry_delay_seconds * (2 ** (attempt - 1)), 2.0)
                        )
        reason = "ALL_PROVIDERS_FAILED:" + ",".join(sorted(set(error_types)))
        return failed_snapshot(
            frozen,
            provider="|".join(provider.name for provider in self.providers),
            reason=reason,
        )


async def _report(callback: ProgressCallback, completed: int, total: int) -> None:
    value = callback(completed, total)
    if inspect.isawaitable(value):
        await value


__all__ = ["PatentSnapshotFetchError", "PatentSnapshotFetchService"]
