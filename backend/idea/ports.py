from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Generic, Mapping, Protocol, TypeVar, runtime_checkable


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
KeyT = TypeVar("KeyT")
EntityT = TypeVar("EntityT")


def _required(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be empty")


def _sha256(value: str) -> None:
    if not _SHA256.fullmatch(value):
        raise ValueError("sha256 must be 64 lowercase hexadecimal characters")


@dataclass(frozen=True)
class ObjectInfo:
    key: str
    size: int
    sha256: str
    content_type: str
    encoding: str

    def __post_init__(self) -> None:
        _required(self.key, "key")
        _required(self.content_type, "content_type")
        _required(self.encoding, "encoding")
        if self.size < 0:
            raise ValueError("size must be non-negative")
        _sha256(self.sha256)


@dataclass(frozen=True)
class JobRequest:
    kind: str
    idempotency_key: str
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        _required(self.kind, "kind")
        _required(self.idempotency_key, "idempotency_key")


@dataclass(frozen=True)
class JobLease:
    job_id: str
    lease_id: str
    worker_id: str
    expires_at: datetime
    request: JobRequest

    def __post_init__(self) -> None:
        _required(self.job_id, "job_id")
        _required(self.lease_id, "lease_id")
        _required(self.worker_id, "worker_id")


@dataclass(frozen=True)
class LimiterLease:
    key: str
    lease_id: str
    owner: str
    expires_at: datetime

    def __post_init__(self) -> None:
        _required(self.key, "key")
        _required(self.lease_id, "lease_id")
        _required(self.owner, "owner")


@dataclass(frozen=True)
class VectorRecord:
    chunk_id: str
    version_id: str
    profile_id: str
    embedding: tuple[float, ...]

    def __post_init__(self) -> None:
        _required(self.chunk_id, "chunk_id")
        _required(self.version_id, "version_id")
        _required(self.profile_id, "profile_id")
        if not self.embedding or not all(math.isfinite(value) for value in self.embedding):
            raise ValueError("embedding must contain finite values")


@dataclass(frozen=True)
class VectorQuery:
    profile_id: str
    embedding: tuple[float, ...]
    allowed_version_ids: tuple[str, ...]
    top_k: int

    def __post_init__(self) -> None:
        _required(self.profile_id, "profile_id")
        if not self.embedding or not all(math.isfinite(value) for value in self.embedding):
            raise ValueError("embedding must contain finite values")
        if not self.allowed_version_ids or any(not value.strip() for value in self.allowed_version_ids):
            raise ValueError("vector queries require an explicit non-empty version scope")
        if self.top_k < 1:
            raise ValueError("top_k must be positive")


@dataclass(frozen=True)
class VectorHit:
    chunk_id: str
    version_id: str
    score: float
    rank: int

    def __post_init__(self) -> None:
        _required(self.chunk_id, "chunk_id")
        _required(self.version_id, "version_id")
        if not math.isfinite(self.score):
            raise ValueError("score must be finite")
        if self.rank < 1:
            raise ValueError("rank must be positive")


@runtime_checkable
class Repository(Protocol, Generic[KeyT, EntityT]):
    """Minimal immutable-record repository boundary."""

    async def get(self, key: KeyT) -> EntityT | None: ...

    async def put_if_absent(self, key: KeyT, entity: EntityT) -> bool: ...

    async def healthcheck(self) -> Mapping[str, Any]: ...


@runtime_checkable
class ObjectStore(Protocol):
    """Content-addressed object storage boundary."""

    async def put_if_absent(
        self,
        key: str,
        content: bytes,
        *,
        expected_sha256: str,
        content_type: str,
        encoding: str,
    ) -> ObjectInfo: ...

    async def get(self, key: str) -> bytes: ...

    async def stat(self, key: str) -> ObjectInfo | None: ...


@runtime_checkable
class JobQueue(Protocol):
    """Durable asynchronous job and lease boundary."""

    async def enqueue(self, request: JobRequest) -> str: ...

    async def claim(self, *, worker_id: str, lease_seconds: int) -> JobLease | None: ...

    async def complete(self, lease: JobLease, result: Mapping[str, Any]) -> None: ...

    async def fail(self, lease: JobLease, *, error_code: str, retryable: bool) -> None: ...

    async def cancel(self, job_id: str) -> bool: ...


@runtime_checkable
class DistributedLimiter(Protocol):
    """Cross-process lease and cooldown boundary."""

    async def acquire(
        self, *, key: str, owner: str, lease_seconds: int
    ) -> LimiterLease | None: ...

    async def release(self, lease: LimiterLease) -> None: ...

    async def defer_until(self, *, key: str, available_at: datetime, reason: str) -> None: ...


@runtime_checkable
class VectorIndex(Protocol):
    """Version-scoped vector storage and search boundary."""

    async def upsert(self, records: tuple[VectorRecord, ...]) -> None: ...

    async def search(self, query: VectorQuery) -> tuple[VectorHit, ...]: ...

    async def healthcheck(self) -> Mapping[str, Any]: ...


__all__ = [
    "DistributedLimiter",
    "JobLease",
    "JobQueue",
    "JobRequest",
    "LimiterLease",
    "ObjectInfo",
    "ObjectStore",
    "Repository",
    "VectorHit",
    "VectorIndex",
    "VectorQuery",
    "VectorRecord",
]
