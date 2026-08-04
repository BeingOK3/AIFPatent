from __future__ import annotations

import hashlib
import json
from datetime import date
from enum import StrEnum

from pydantic import Field, model_validator

from idea.providers.base import FetchedDocument

from .family_resolution import BibliographicPublication
from .publication_freeze import FrozenPublication, FrozenPublicationSet
from .scope import ScopeModel


class PatentSnapshotError(ValueError):
    pass


class PatentSnapshotStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    PROVIDER_FAILED = "PROVIDER_FAILED"


class PatentSnapshot(ScopeModel):
    publication_id: str = Field(pattern=r"^PUB-[0-9a-f]{16}$")
    status: PatentSnapshotStatus
    publication_number: str | None = Field(default=None, max_length=100)
    application_number: str | None = Field(default=None, max_length=100)
    family_id: str | None = Field(default=None, max_length=300)
    title: str = Field(default="", max_length=2000)
    applicants: tuple[str, ...] = Field(default=(), max_length=100)
    priority_date: date | None = None
    filing_date: date | None = None
    publication_date: date | None = None
    url: str = Field(min_length=1, max_length=4000)
    language: str = Field(default="en", min_length=1, max_length=20)
    abstract_text: str = Field(default="", max_length=1_000_000)
    provider: str = Field(min_length=1, max_length=100)
    failure_reason: str | None = Field(default=None, max_length=1000)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_terminal_status(self) -> "PatentSnapshot":
        if self.status == PatentSnapshotStatus.AVAILABLE and self.failure_reason is not None:
            raise ValueError("available snapshot cannot have a failure reason")
        if self.status == PatentSnapshotStatus.PROVIDER_FAILED and not self.failure_reason:
            raise ValueError("failed snapshot requires a failure reason")
        semantic = self.model_dump(mode="json", exclude={"content_hash"})
        if self.content_hash != _hash(semantic):
            raise ValueError("snapshot content hash mismatch")
        return self


class PatentSnapshotSet(ScopeModel):
    run_id: str = Field(min_length=1, max_length=100)
    snapshots: tuple[PatentSnapshot, ...]

    @model_validator(mode="after")
    def validate_partition(self) -> "PatentSnapshotSet":
        identifiers = [item.publication_id for item in self.snapshots]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("snapshot publication IDs must be unique")
        if identifiers != sorted(identifiers):
            raise ValueError("snapshots must be sorted by publication ID")
        return self


def snapshot_from_document(
    frozen: FrozenPublication,
    document: FetchedDocument,
) -> PatentSnapshot:
    requested = _normalize_publication(frozen.publication_number)
    returned = _normalize_publication(document.publication_number)
    if requested and returned != requested:
        raise PatentSnapshotError(
            f"provider returned {returned or 'blank'} for requested {requested}"
        )
    applicants = _unique_names(
        document.assignees or ([document.assignee] if document.assignee else [])
    )
    values = {
        "publication_id": frozen.publication_id,
        "status": PatentSnapshotStatus.AVAILABLE,
        "publication_number": returned or requested or None,
        "application_number": _normalize_publication(document.application_number) or None,
        "family_id": document.family_id,
        "title": document.title or frozen.title,
        "applicants": applicants,
        "priority_date": _date(document.priority_date) or frozen.priority_date,
        "filing_date": _date(document.filing_date) or frozen.filing_date,
        "publication_date": _date(document.publication_date) or frozen.publication_date,
        "url": document.url or frozen.url,
        "language": document.language,
        "abstract_text": document.abstract_text,
        "provider": document.provider,
        "failure_reason": None,
    }
    return PatentSnapshot(**values, content_hash=_hash(_json(values)))


def failed_snapshot(
    frozen: FrozenPublication,
    *,
    provider: str,
    reason: str,
) -> PatentSnapshot:
    values = {
        "publication_id": frozen.publication_id,
        "status": PatentSnapshotStatus.PROVIDER_FAILED,
        "publication_number": frozen.publication_number,
        "application_number": frozen.application_number,
        "family_id": frozen.family_id,
        "title": frozen.title,
        "applicants": (frozen.assignee,) if frozen.assignee else (),
        "priority_date": frozen.priority_date,
        "filing_date": frozen.filing_date,
        "publication_date": frozen.publication_date,
        "url": frozen.url,
        "language": "en",
        "abstract_text": "",
        "provider": provider,
        "failure_reason": reason.strip()[:1000] or "PROVIDER_FAILED",
    }
    return PatentSnapshot(**values, content_hash=_hash(_json(values)))


def make_snapshot_set(
    frozen: FrozenPublicationSet,
    snapshots: tuple[PatentSnapshot, ...],
) -> PatentSnapshotSet:
    expected = {item.publication_id for item in frozen.publications}
    actual = {item.publication_id for item in snapshots}
    if expected != actual or len(actual) != len(snapshots):
        raise PatentSnapshotError("snapshots must exactly partition frozen publications")
    return PatentSnapshotSet(
        run_id=frozen.run_id,
        snapshots=tuple(sorted(snapshots, key=lambda item: item.publication_id)),
    )


def snapshot_bibliography(
    snapshot_set: PatentSnapshotSet,
) -> tuple[BibliographicPublication, ...]:
    return tuple(
        BibliographicPublication(
            publication_id=item.publication_id,
            publication_number=item.publication_number or item.publication_id,
            application_number=item.application_number,
            family_id=item.family_id,
        )
        for item in snapshot_set.snapshots
    )


def _unique_names(values: list[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = " ".join(value.split())
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return tuple(result)


def _normalize_publication(value: str | None) -> str:
    return "" if not value else "".join(value.upper().split())


def _date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _json(value: dict) -> dict:
    return {
        key: item.value if isinstance(item, StrEnum) else item.isoformat()
        if isinstance(item, date)
        else list(item) if isinstance(item, tuple)
        else item
        for key, item in value.items()
    }


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


__all__ = [
    "PatentSnapshot",
    "PatentSnapshotError",
    "PatentSnapshotSet",
    "PatentSnapshotStatus",
    "failed_snapshot",
    "make_snapshot_set",
    "snapshot_bibliography",
    "snapshot_from_document",
]
