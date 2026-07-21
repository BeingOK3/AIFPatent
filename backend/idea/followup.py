from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Sequence


class FollowupError(RuntimeError):
    """Raised when follow-up state or frozen evidence scope cannot be trusted."""


class FollowupMode(str, Enum):
    EVIDENCE_QA = "EVIDENCE_QA"
    DESIGN_AROUND = "DESIGN_AROUND"
    NEW_RESEARCH = "NEW_RESEARCH"


class ThreadStatus(str, Enum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class TurnStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_LIMITATIONS = "COMPLETED_WITH_LIMITATIONS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL_TURN_STATUSES = frozenset(
    {
        TurnStatus.COMPLETED,
        TurnStatus.COMPLETED_WITH_LIMITATIONS,
        TurnStatus.FAILED,
        TurnStatus.CANCELLED,
    }
)


@dataclass(frozen=True)
class FollowupScopeDocument:
    document_id: str
    version_id: str
    publication_number: str
    content_sha256: str

    def __post_init__(self) -> None:
        if not self.document_id.strip() or not self.version_id.strip():
            raise ValueError("follow-up scope identifiers must not be empty")
        if not self.publication_number.strip():
            raise ValueError("follow-up scope publication number must not be empty")
        if len(self.content_sha256) != 64 or any(
            value not in "0123456789abcdef" for value in self.content_sha256
        ):
            raise ValueError("follow-up scope content hash must be lowercase SHA-256")


@dataclass(frozen=True)
class FollowupScope:
    documents: tuple[FollowupScopeDocument, ...]
    corpus_snapshot_hash: str

    @classmethod
    def freeze(cls, documents: Sequence[FollowupScopeDocument]) -> "FollowupScope":
        ordered = tuple(
            sorted(documents, key=lambda item: (item.publication_number, item.version_id))
        )
        if not ordered:
            raise FollowupError("follow-up scope requires at least one READY deep-reviewed Version")
        if len({item.version_id for item in ordered}) != len(ordered):
            raise FollowupError("follow-up scope Version IDs must be unique")
        if len({item.publication_number for item in ordered}) != len(ordered):
            raise FollowupError("follow-up scope publication numbers must be unique")
        manifest = [
            {
                "version_id": item.version_id,
                "publication_number": item.publication_number,
                "content_sha256": item.content_sha256,
            }
            for item in sorted(ordered, key=lambda item: item.version_id)
        ]
        encoded = json.dumps(
            manifest, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        )
        return cls(
            documents=ordered,
            corpus_snapshot_hash=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        )

    @property
    def version_ids(self) -> tuple[str, ...]:
        return tuple(item.version_id for item in self.documents)

    @property
    def publication_numbers(self) -> tuple[str, ...]:
        return tuple(item.publication_number for item in self.documents)

    def select_publications(self, publications: Sequence[str]) -> "FollowupScope":
        requested = tuple(dict.fromkeys(value.strip() for value in publications if value.strip()))
        if not requested:
            return self
        available = {item.publication_number: item for item in self.documents}
        missing = set(requested) - set(available)
        if missing:
            raise FollowupError(
                "selected publications are outside the source Run: " + ", ".join(sorted(missing))
            )
        return self.freeze([available[value] for value in requested])

    def as_json(self) -> dict[str, Any]:
        return {
            "version_ids": list(self.version_ids),
            "publication_numbers": list(self.publication_numbers),
            "documents": [
                {
                    "document_id": item.document_id,
                    "version_id": item.version_id,
                    "publication_number": item.publication_number,
                    "content_sha256": item.content_sha256,
                }
                for item in self.documents
            ],
        }

    @classmethod
    def from_json(cls, value: dict[str, Any], snapshot_hash: str) -> "FollowupScope":
        raw_documents = value.get("documents")
        if not isinstance(raw_documents, list):
            raise FollowupError("stored follow-up scope documents are invalid")
        scope = cls.freeze(
            [
                FollowupScopeDocument(
                    document_id=str(item["document_id"]),
                    version_id=str(item["version_id"]),
                    publication_number=str(item["publication_number"]),
                    content_sha256=str(item["content_sha256"]),
                )
                for item in raw_documents
                if isinstance(item, dict)
            ]
        )
        if scope.corpus_snapshot_hash != snapshot_hash:
            raise FollowupError("stored follow-up scope hash does not match its documents")
        return scope


@dataclass(frozen=True)
class FollowupThread:
    thread_id: str
    run_id: str
    title: str
    status: ThreadStatus
    default_mode: FollowupMode
    scope: FollowupScope
    created_at: int
    updated_at: int


@dataclass(frozen=True)
class FollowupTurn:
    turn_id: str
    thread_id: str
    parent_turn_id: str | None
    status: TurnStatus
    mode: FollowupMode
    question_text: str
    question_hash: str
    scope: FollowupScope
    plan: dict[str, Any] | None
    answer: dict[str, Any] | None
    model: str
    prompt_version: str
    retriever_version: str
    limitations: tuple[str, ...]
    error_code: str | None
    error_message: str | None
    created_at: int
    started_at: int | None
    completed_at: int | None


@dataclass(frozen=True)
class FollowupCitationDraft:
    chunk_id: str
    quote_text: str
    start_offset: int | None
    end_offset: int | None
    answer_path: str

    def __post_init__(self) -> None:
        if not self.chunk_id.strip() or not self.quote_text:
            raise ValueError("follow-up Citation requires a Chunk and quote")
        if not self.answer_path.strip():
            raise ValueError("follow-up Citation answer path must not be empty")
        if (self.start_offset is None) != (self.end_offset is None):
            raise ValueError("follow-up Citation offsets must both be present or absent")
        if self.start_offset is not None and (
            self.start_offset < 0 or self.end_offset < self.start_offset
        ):
            raise ValueError("follow-up Citation offsets are invalid")


@dataclass(frozen=True)
class FollowupCitation:
    citation_id: str
    turn_id: str
    chunk_id: str
    publication_number: str
    section_type: str
    section_label: str
    quote_text: str
    quote_hash: str
    start_offset: int | None
    end_offset: int | None
    answer_path: str


def question_hash(question: str) -> str:
    normalized = " ".join(question.split())
    if not normalized:
        raise ValueError("follow-up question must not be empty")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


__all__ = [
    "FollowupError",
    "FollowupCitation",
    "FollowupCitationDraft",
    "FollowupMode",
    "FollowupScope",
    "FollowupScopeDocument",
    "FollowupThread",
    "FollowupTurn",
    "TERMINAL_TURN_STATUSES",
    "ThreadStatus",
    "TurnStatus",
    "question_hash",
]
