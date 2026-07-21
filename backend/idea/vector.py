from __future__ import annotations

import math
import statistics
import time
from dataclasses import dataclass
from typing import Protocol

from .chunks import PatentChunk


class VectorSearchError(RuntimeError):
    """Raised when vector retrieval cannot preserve its profile or Version scope."""


@dataclass(frozen=True)
class VectorSearchRequest:
    query_id: str
    embedding: tuple[float, ...]
    profile_id: str
    allowed_version_ids: tuple[str, ...]
    section_types: tuple[str, ...] | None = None
    limit: int = 30

    def __post_init__(self) -> None:
        if not self.query_id.strip() or not self.profile_id.strip():
            raise ValueError("vector query ID and profile ID must not be empty")
        if not self.embedding or any(not math.isfinite(value) for value in self.embedding):
            raise ValueError("vector query embedding must be non-empty and finite")
        if not self.allowed_version_ids:
            raise ValueError("vector search requires allowed Version IDs")
        if len(set(self.allowed_version_ids)) != len(self.allowed_version_ids):
            raise ValueError("allowed Version IDs must be unique")
        if any(not value.strip() for value in self.allowed_version_ids):
            raise ValueError("allowed Version IDs must not be empty")
        if self.section_types is not None and (
            not self.section_types or any(not value.strip() for value in self.section_types)
        ):
            raise ValueError("section types must be omitted or non-empty")
        if not 1 <= self.limit <= 50:
            raise ValueError("vector search limit must be between 1 and 50")


@dataclass(frozen=True)
class VectorHit:
    query_id: str
    rank: int
    cosine_distance: float
    index_mode: str
    chunk: PatentChunk


class VectorIndex(Protocol):
    async def search(self, request: VectorSearchRequest) -> tuple[VectorHit, ...]: ...


@dataclass(frozen=True)
class VectorBenchmarkResult:
    request_count: int
    hit_count: int
    min_ms: float
    median_ms: float
    p95_ms: float
    max_ms: float
    index_mode: str = "exact"


async def benchmark_vector_index(
    index: VectorIndex, requests: tuple[VectorSearchRequest, ...]
) -> VectorBenchmarkResult:
    if not requests:
        raise ValueError("vector benchmark requires at least one request")
    durations: list[float] = []
    hit_count = 0
    for request in requests:
        started = time.perf_counter()
        hits = await index.search(request)
        durations.append((time.perf_counter() - started) * 1000)
        hit_count += len(hits)
        if any(hit.index_mode != "exact" for hit in hits):
            raise VectorSearchError("vector benchmark received a non-exact result")
    ordered = sorted(durations)
    p95_index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return VectorBenchmarkResult(
        request_count=len(requests),
        hit_count=hit_count,
        min_ms=ordered[0],
        median_ms=statistics.median(ordered),
        p95_ms=ordered[p95_index],
        max_ms=ordered[-1],
    )


__all__ = [
    "VectorBenchmarkResult",
    "VectorHit",
    "VectorIndex",
    "VectorSearchError",
    "VectorSearchRequest",
    "benchmark_vector_index",
]
