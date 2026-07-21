from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .ports import ObjectStore, Repository
from .providers import FetchedDocument


_PUBLICATION_PART = re.compile(r"[^A-Za-z0-9._-]+")


class CorpusError(RuntimeError):
    """Raised when a corpus version cannot be safely created or read."""


@dataclass(frozen=True)
class CorpusVersion:
    version_id: str
    publication_number: str
    language: str
    provider: str
    object_key: str
    content_sha256: str
    normalized_size: int
    created_at: datetime
    status: str = "READY"


def _canonical_document(document: FetchedDocument) -> bytes:
    """Serialize only validated provider data, with stable key ordering."""
    payload = document.model_dump(mode="json", exclude_none=False)
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _safe_publication_number(publication_number: str) -> str:
    value = _PUBLICATION_PART.sub("-", publication_number.strip()).strip("-")
    if not value:
        raise CorpusError("publication number cannot produce an object key")
    return value[:180]


class PatentCorpusService:
    """Create immutable, content-addressed document versions.

    Chunking and indexing are deliberately separate work units. A version is
    usable only after the immutable normalized Blob has been written and the
    repository record is present.
    """

    def __init__(
        self,
        *,
        versions: Repository[str, CorpusVersion],
        objects: ObjectStore,
        corpus_prefix: str = "patent-corpus",
    ) -> None:
        if not corpus_prefix or corpus_prefix.startswith("/"):
            raise ValueError("corpus_prefix must be a relative non-empty path")
        self.versions = versions
        self.objects = objects
        self.corpus_prefix = corpus_prefix.rstrip("/")

    async def ingest(self, document: FetchedDocument) -> CorpusVersion:
        normalized = _canonical_document(document)
        content_sha256 = hashlib.sha256(normalized).hexdigest()
        identity = "|".join(
            (document.publication_number.strip(), document.language.strip().lower(), content_sha256)
        )
        version_id = "cv-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
        object_key = (
            f"{self.corpus_prefix}/{_safe_publication_number(document.publication_number)}"
            f"/{content_sha256}.json"
        )
        await self.objects.put_if_absent(
            object_key,
            normalized,
            expected_sha256=content_sha256,
            content_type="application/json",
            encoding="identity",
        )
        version = CorpusVersion(
            version_id=version_id,
            publication_number=document.publication_number,
            language=document.language,
            provider=document.provider,
            object_key=object_key,
            content_sha256=content_sha256,
            normalized_size=len(normalized),
            created_at=datetime.now(timezone.utc),
        )
        inserted = await self.versions.put_if_absent(version_id, version)
        if not inserted:
            existing = await self.versions.get(version_id)
            if existing is None or any(
                (
                    getattr(existing, field) != getattr(version, field)
                    for field in (
                        "version_id",
                        "publication_number",
                        "language",
                        "provider",
                        "object_key",
                        "content_sha256",
                        "normalized_size",
                        "status",
                    )
                )
            ):
                raise CorpusError("corpus version identity conflicts with existing record")
            version = existing
        return version

    async def get_ready(self, version_id: str) -> CorpusVersion:
        version = await self.versions.get(version_id)
        if version is None:
            raise CorpusError("corpus version does not exist")
        if version.status != "READY":
            raise CorpusError("corpus version is not ready")
        object_info = await self.objects.stat(version.object_key)
        if object_info is None or object_info.sha256 != version.content_sha256:
            raise CorpusError("corpus version blob is missing or corrupt")
        return version

    async def snapshot_hash(self, version_ids: tuple[str, ...]) -> str:
        if not version_ids or len(set(version_ids)) != len(version_ids):
            raise ValueError("snapshot requires unique non-empty version IDs")
        versions = [await self.get_ready(version_id) for version_id in sorted(version_ids)]
        manifest: list[dict[str, Any]] = [
            {
                "version_id": version.version_id,
                "publication_number": version.publication_number,
                "content_sha256": version.content_sha256,
            }
            for version in versions
        ]
        encoded = json.dumps(manifest, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = ["CorpusError", "CorpusVersion", "PatentCorpusService"]
