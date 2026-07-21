from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

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
    document_id: str | None = None


@dataclass(frozen=True)
class CorpusRunLink:
    run_id: str
    document_id: str
    version_id: str
    corpus_availability: str = "READY"
    deep_reviewed: bool = False
    linked_at: datetime | None = None


@dataclass(frozen=True)
class CorpusIngestResult:
    version_ids: tuple[str, ...]
    snapshot_hash: str


@dataclass(frozen=True)
class CorpusVersionSource:
    source_id: str
    version_id: str
    provider: str
    source_url: str
    retrieved_at: datetime
    raw_response_hash: str | None
    parser_version: str
    metadata: dict[str, Any]


class CorpusRunLinkRepository(Protocol):
    async def get(self, run_id: str, document_id: str) -> CorpusRunLink | None: ...

    async def put_if_absent(self, link: CorpusRunLink) -> bool: ...

    async def mark_deep_reviewed(
        self, run_id: str, document_ids: tuple[str, ...]
    ) -> None: ...


class CorpusVersionSourceRepository(Protocol):
    async def put_if_absent(self, source: CorpusVersionSource) -> bool: ...


class CorpusPrerequisiteRepository(Protocol):
    async def prepare(
        self, run_id: str, document_ids: tuple[str, ...]
    ) -> None: ...


def _canonical_document(document: FetchedDocument) -> bytes:
    """Serialize stable patent content, excluding retrieval/source metadata."""
    payload = document.model_dump(
        mode="json",
        exclude={"provider", "url", "raw_metadata"},
        exclude_none=False,
    )
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
        sources: CorpusVersionSourceRepository | None = None,
        corpus_prefix: str = "patent-corpus",
    ) -> None:
        if not corpus_prefix or corpus_prefix.startswith("/"):
            raise ValueError("corpus_prefix must be a relative non-empty path")
        self.versions = versions
        self.objects = objects
        self.sources = sources
        self.corpus_prefix = corpus_prefix.rstrip("/")

    async def ingest(
        self, document: FetchedDocument, *, document_id: str | None = None
    ) -> CorpusVersion:
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
            document_id=document_id,
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
                        "object_key",
                        "content_sha256",
                        "normalized_size",
                        "status",
                        "document_id",
                    )
                )
            ):
                raise CorpusError("corpus version identity conflicts with existing record")
            version = existing
        if self.sources is not None:
            raw_metadata = json.dumps(
                document.raw_metadata,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            metadata_hash = hashlib.sha256(raw_metadata).hexdigest() if document.raw_metadata else None
            source_identity = "|".join(
                (
                    version.version_id,
                    document.provider,
                    document.url,
                    metadata_hash or "",
                    "aifpatent-corpus/1",
                )
            )
            await self.sources.put_if_absent(
                CorpusVersionSource(
                    source_id="cvs-" + hashlib.sha256(source_identity.encode("utf-8")).hexdigest(),
                    version_id=version.version_id,
                    provider=document.provider,
                    source_url=document.url,
                    retrieved_at=datetime.now(timezone.utc),
                    raw_response_hash=None,
                    parser_version="aifpatent-corpus/1",
                    metadata={
                        "raw_metadata": document.raw_metadata,
                    },
                )
            )
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


class PatentCorpusIngestService:
    """Persist fetched documents and freeze their immutable versions for one run."""

    def __init__(
        self,
        *,
        corpus: PatentCorpusService,
        run_links: CorpusRunLinkRepository,
        prerequisites: CorpusPrerequisiteRepository | None = None,
    ) -> None:
        self.corpus = corpus
        self.run_links = run_links
        self.prerequisites = prerequisites

    async def ingest_many(
        self,
        *,
        run_id: str,
        documents: tuple[FetchedDocument, ...],
        document_ids: dict[str, str],
    ) -> CorpusIngestResult:
        if not run_id.strip():
            raise ValueError("run_id is required")
        if not documents:
            raise ValueError("at least one fetched document is required")

        publications = [item.publication_number for item in documents]
        if len(set(publications)) != len(publications):
            raise CorpusError("fetched document publication numbers must be unique")
        missing = [value for value in publications if not document_ids.get(value)]
        if missing:
            raise CorpusError("every fetched document requires a persisted document ID")

        persisted_ids = tuple(document_ids[value] for value in publications)
        if self.prerequisites is not None:
            await self.prerequisites.prepare(run_id, persisted_ids)

        version_ids: list[str] = []
        for document in documents:
            document_id = document_ids[document.publication_number]
            version = await self.corpus.ingest(document, document_id=document_id)
            await self.corpus.get_ready(version.version_id)
            link = CorpusRunLink(
                run_id=run_id,
                document_id=document_id,
                version_id=version.version_id,
                linked_at=datetime.now(timezone.utc),
            )
            inserted = await self.run_links.put_if_absent(link)
            if not inserted:
                existing = await self.run_links.get(run_id, document_id)
                if existing is None:
                    raise CorpusError("run corpus link disappeared after a conflicting write")
                if existing.version_id != version.version_id:
                    raise CorpusError("run document is frozen to a different corpus version")
                if existing.corpus_availability != "READY":
                    raise CorpusError("run corpus version is not ready")
            version_ids.append(version.version_id)

        frozen = tuple(version_ids)
        return CorpusIngestResult(
            version_ids=frozen,
            snapshot_hash=await self.corpus.snapshot_hash(frozen),
        )

    async def mark_deep_reviewed(
        self, run_id: str, document_ids: tuple[str, ...]
    ) -> None:
        if not document_ids:
            raise ValueError("at least one document ID is required")
        await self.run_links.mark_deep_reviewed(run_id, document_ids)


__all__ = [
    "CorpusError",
    "CorpusIngestResult",
    "CorpusRunLink",
    "CorpusRunLinkRepository",
    "CorpusPrerequisiteRepository",
    "CorpusVersion",
    "CorpusVersionSource",
    "CorpusVersionSourceRepository",
    "PatentCorpusIngestService",
    "PatentCorpusService",
]
