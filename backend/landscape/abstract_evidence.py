from __future__ import annotations

import hashlib
import re
from enum import StrEnum

from pydantic import Field, model_validator

from idea.providers.base import FetchedDocument

from .scope import ScopeModel


class AbstractStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    MISSING = "MISSING"
    INVALID = "INVALID"
    PROVIDER_FAILED = "PROVIDER_FAILED"


class AbstractEvidence(ScopeModel):
    publication_id: str = Field(min_length=1)
    status: AbstractStatus
    title: str = ""
    abstract_text: str = ""
    normalized_abstract: str = ""
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = ()
    unresolved_reason: str | None = None

    @model_validator(mode="after")
    def validate_terminal(self) -> "AbstractEvidence":
        if self.status == AbstractStatus.AVAILABLE:
            if not self.normalized_abstract or not self.evidence_ids:
                raise ValueError("available abstract requires evidence sentences")
            if self.unresolved_reason is not None:
                raise ValueError("available abstract cannot have unresolved reason")
        else:
            if self.evidence_ids:
                raise ValueError("unavailable abstract cannot have evidence")
            if not self.unresolved_reason:
                raise ValueError("unavailable abstract requires unresolved reason")
        return self


def abstract_from_document(publication_id: str, document: FetchedDocument) -> AbstractEvidence:
    if not document.abstract_text or not document.abstract_text.strip():
        return _unresolved(publication_id, document.provider, AbstractStatus.MISSING, "ABSTRACT_MISSING", document.title)
    normalized = _normalize(document.abstract_text)
    if len(normalized) < 20:
        return _unresolved(publication_id, document.provider, AbstractStatus.INVALID, "ABSTRACT_INSUFFICIENT", document.title)
    return _available(publication_id, document.provider, document.title, document.abstract_text, normalized)


def abstract_provider_failure(publication_id: str, provider: str, reason: str = "PROVIDER_FAILED") -> AbstractEvidence:
    return _unresolved(publication_id, provider, AbstractStatus.PROVIDER_FAILED, reason, "")


def _available(publication_id, provider, title, original, normalized):
    sentences = tuple(sentence for sentence in re.split(r"(?<=[。！？!?\.])\s*", normalized) if sentence)
    evidence_ids = tuple(f"EV-{publication_id}-A{index:02d}" for index, _ in enumerate(sentences, start=1))
    return AbstractEvidence(
        publication_id=publication_id,
        status=AbstractStatus.AVAILABLE,
        title=title,
        abstract_text=original,
        normalized_abstract=normalized,
        content_hash=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        provider=provider,
        evidence_ids=evidence_ids,
    )


def _unresolved(publication_id, provider, status, reason, title):
    return AbstractEvidence(
        publication_id=publication_id,
        status=status,
        title=title,
        content_hash=hashlib.sha256(b"").hexdigest(),
        provider=provider,
        unresolved_reason=reason,
    )


def _normalize(value: str) -> str:
    return " ".join(value.replace("\r", " ").replace("\n", " ").split())


__all__ = ["AbstractEvidence", "AbstractStatus", "abstract_from_document", "abstract_provider_failure"]
