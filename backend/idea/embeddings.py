from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from .config import EmbeddingSettings


EmbeddingTransport = Callable[[dict[str, Any], dict[str, str]], Awaitable[dict[str, Any]]]


class EmbeddingError(RuntimeError):
    """Raised when an embedding result cannot be safely indexed or reused."""


@dataclass(frozen=True)
class EmbeddingProfile:
    provider: str
    model: str
    dimensions: int
    normalization: str = "l2"

    def __post_init__(self) -> None:
        if not self.provider.strip() or not self.model.strip():
            raise ValueError("embedding provider and model must not be empty")
        if self.dimensions < 1:
            raise ValueError("embedding dimensions must be positive")
        if self.normalization != "l2":
            raise ValueError("only l2 embedding normalization is supported")

    @property
    def profile_id(self) -> str:
        payload = json.dumps(
            {
                "provider": self.provider,
                "model": self.model,
                "dimensions": self.dimensions,
                "normalization": self.normalization,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return "ep-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class CachedEmbedding:
    text_hash: str
    profile_id: str
    embedding: tuple[float, ...]
    vector_norm: float


class EmbeddingProvider(Protocol):
    provider: str
    model: str
    dimensions: int

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


class EmbeddingCache(Protocol):
    async def ensure_profile(self, profile: EmbeddingProfile) -> None: ...

    async def get_many(
        self, profile_id: str, text_hashes: tuple[str, ...]
    ) -> dict[str, CachedEmbedding]: ...

    async def put_many(self, values: tuple[CachedEmbedding, ...]) -> None: ...


class OpenAICompatibleEmbeddingProvider:
    """Deployment-scoped adapter; it never uses per-Run chat BYOK credentials."""

    def __init__(
        self,
        settings: EmbeddingSettings,
        *,
        transport: EmbeddingTransport | None = None,
    ) -> None:
        self.settings = settings
        self.provider = settings.provider
        self.model = settings.model
        self.dimensions = settings.dimensions
        self._transport = transport or self._http_transport

    def api_key(self) -> str:
        value = os.environ.get(self.settings.api_key_env, "").strip()
        if not value:
            raise EmbeddingError("deployment embedding credential is not configured")
        return value

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts or any(not text.strip() for text in texts):
            raise EmbeddingError("embedding document texts must be non-empty")
        return await self._embed(texts)

    async def embed_query(self, text: str) -> list[float]:
        if not text.strip():
            raise EmbeddingError("embedding query text must be non-empty")
        return (await self._embed([text]))[0]

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        response = await self._transport(
            {"model": self.model, "input": texts},
            {
                "Authorization": f"Bearer {self.api_key()}",
                "Content-Type": "application/json",
            },
        )
        raw = response.get("data")
        if not isinstance(raw, list) or len(raw) != len(texts):
            raise EmbeddingError("embedding provider returned an invalid item count")
        ordered: list[list[float] | None] = [None] * len(texts)
        for item in raw:
            if not isinstance(item, dict):
                raise EmbeddingError("embedding provider returned an invalid item")
            index = item.get("index")
            vector = item.get("embedding")
            if not isinstance(index, int) or not 0 <= index < len(texts):
                raise EmbeddingError("embedding provider returned an invalid index")
            if ordered[index] is not None or not isinstance(vector, list):
                raise EmbeddingError("embedding provider returned duplicate or invalid vectors")
            try:
                ordered[index] = [float(value) for value in vector]
            except (TypeError, ValueError) as exc:
                raise EmbeddingError("embedding provider returned a non-numeric vector") from exc
        if any(value is None for value in ordered):
            raise EmbeddingError("embedding provider response is incomplete")
        return [value for value in ordered if value is not None]

    async def _http_transport(
        self, payload: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]:
        endpoint = str(self.settings.base_url).rstrip("/") + "/embeddings"
        try:
            async with httpx.AsyncClient(
                timeout=self.settings.timeout_seconds, trust_env=True
            ) as client:
                response = await client.post(endpoint, json=payload, headers=headers)
                response.raise_for_status()
                parsed = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise EmbeddingError(
                f"embedding provider request failed: {type(exc).__name__}"
            ) from exc
        if not isinstance(parsed, dict):
            raise EmbeddingError("embedding provider returned a non-object response")
        return parsed


class EmbeddingService:
    """Deduplicate by exact Chunk text hash and persist normalized vectors."""

    def __init__(
        self,
        provider: EmbeddingProvider,
        cache: EmbeddingCache,
        *,
        normalization: str = "l2",
        batch_size: int = 32,
    ) -> None:
        if not 1 <= batch_size <= 256:
            raise ValueError("embedding batch size must be between 1 and 256")
        self.provider = provider
        self.cache = cache
        self.batch_size = batch_size
        self.profile = EmbeddingProfile(
            provider=provider.provider,
            model=provider.model,
            dimensions=provider.dimensions,
            normalization=normalization,
        )

    async def embed_documents(self, texts: Sequence[str]) -> tuple[CachedEmbedding, ...]:
        if not texts or any(not text.strip() for text in texts):
            raise EmbeddingError("embedding document texts must be non-empty")
        await self.cache.ensure_profile(self.profile)
        text_hashes = tuple(self.text_hash(text) for text in texts)
        unique_hashes = tuple(dict.fromkeys(text_hashes))
        text_by_hash = dict(zip(text_hashes, texts, strict=False))
        cached = await self.cache.get_many(self.profile.profile_id, unique_hashes)
        self._validate_cached(cached, unique_hashes)

        missing = [value for value in unique_hashes if value not in cached]
        for start in range(0, len(missing), self.batch_size):
            batch_hashes = missing[start : start + self.batch_size]
            vectors = await self.provider.embed_documents(
                [text_by_hash[value] for value in batch_hashes]
            )
            if len(vectors) != len(batch_hashes):
                raise EmbeddingError("embedding provider returned an invalid batch size")
            records = tuple(
                self._record(text_hash, vector)
                for text_hash, vector in zip(batch_hashes, vectors, strict=True)
            )
            await self.cache.put_many(records)
            cached.update({item.text_hash: item for item in records})
        return tuple(cached[value] for value in text_hashes)

    async def embed_query(self, text: str) -> tuple[float, ...]:
        if not text.strip():
            raise EmbeddingError("embedding query text must be non-empty")
        vector = await self.provider.embed_query(text)
        return self._normalize(vector)

    @staticmethod
    def text_hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _record(self, text_hash: str, vector: Sequence[float]) -> CachedEmbedding:
        normalized = self._normalize(vector)
        return CachedEmbedding(
            text_hash=text_hash,
            profile_id=self.profile.profile_id,
            embedding=normalized,
            vector_norm=math.sqrt(sum(value * value for value in normalized)),
        )

    def _normalize(self, vector: Sequence[float]) -> tuple[float, ...]:
        if len(vector) != self.profile.dimensions:
            raise EmbeddingError(
                f"embedding dimensions mismatch: expected {self.profile.dimensions}, got {len(vector)}"
            )
        values = tuple(float(value) for value in vector)
        if any(not math.isfinite(value) for value in values):
            raise EmbeddingError("embedding contains a non-finite value")
        norm = math.sqrt(sum(value * value for value in values))
        if norm <= 0:
            raise EmbeddingError("embedding vector norm must be positive")
        return tuple(value / norm for value in values)

    def _validate_cached(
        self,
        cached: dict[str, CachedEmbedding],
        requested_hashes: tuple[str, ...],
    ) -> None:
        if not set(cached).issubset(requested_hashes):
            raise EmbeddingError("embedding cache returned an unrequested text hash")
        for text_hash, record in cached.items():
            if record.text_hash != text_hash or record.profile_id != self.profile.profile_id:
                raise EmbeddingError("embedding cache returned mismatched identity")
            normalized = self._normalize(record.embedding)
            if any(
                not math.isclose(left, right, rel_tol=1e-6, abs_tol=1e-6)
                for left, right in zip(record.embedding, normalized, strict=True)
            ) or not math.isclose(record.vector_norm, 1.0, rel_tol=1e-6, abs_tol=1e-6):
                raise EmbeddingError("embedding cache returned a non-normalized vector")


__all__ = [
    "CachedEmbedding",
    "EmbeddingCache",
    "EmbeddingError",
    "EmbeddingProfile",
    "EmbeddingProvider",
    "EmbeddingService",
    "OpenAICompatibleEmbeddingProvider",
]
