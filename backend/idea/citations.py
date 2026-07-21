from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping


class CitationVerificationError(RuntimeError):
    """Raised when a Citation binding differs from its immutable Chunk."""


@dataclass(frozen=True)
class ModelCitationSelection:
    feature_id: str
    alias: str
    chunk_id: str


@dataclass(frozen=True)
class VerifiedCitation:
    context_id: str
    feature_id: str
    alias: str
    chunk_id: str
    version_id: str
    publication_number: str
    section_type: str
    section_label: str
    claim_number: int | None
    start_offset: int
    end_offset: int
    text_hash: str
    excerpt: str
    corpus_snapshot_hash: str
    prompt_version: str
    retriever_version: str
    context_hash: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CitationVerifier:
    _ALIAS = re.compile(r"^C[1-9][0-9]*$")
    _FIELDS = (
        "chunk_id", "version_id", "publication_number", "section_type",
        "section_label", "claim_number", "start_offset", "end_offset", "text_hash",
    )

    @classmethod
    def verify(
        cls,
        binding: Mapping[str, Any],
        chunk: Mapping[str, Any],
        *,
        context_provenance: Mapping[str, Any] | None = None,
    ) -> VerifiedCitation:
        alias = str(binding.get("alias") or "")
        if cls._ALIAS.fullmatch(alias) is None:
            raise CitationVerificationError("Citation alias must use C1..Cn")
        for field in cls._FIELDS:
            if binding.get(field) != chunk.get(field):
                raise CitationVerificationError(f"Citation {field} does not match Chunk")
        excerpt = str(binding.get("excerpt") or "")
        if not excerpt or excerpt != str(chunk.get("text") or ""):
            raise CitationVerificationError("Citation excerpt does not match Chunk text")
        digest = hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
        if digest != binding.get("text_hash"):
            raise CitationVerificationError("Citation excerpt hash does not match text_hash")
        start = binding.get("start_offset")
        end = binding.get("end_offset")
        if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end < start:
            raise CitationVerificationError("Citation offsets are invalid")
        context_id = str(binding.get("context_id") or "")
        feature_id = str(binding.get("feature_id") or "")
        if not context_id.startswith("CTX-") or not re.fullmatch(r"F[1-9][0-9]*", feature_id):
            raise CitationVerificationError("Citation context or feature identity is invalid")
        provenance = dict(context_provenance or {})
        corpus_snapshot_hash = str(provenance.get("corpus_snapshot_hash") or "")
        context_hash = str(provenance.get("context_hash") or "")
        prompt_version = str(provenance.get("prompt_version") or "")
        retriever_version = str(provenance.get("retriever_version") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", corpus_snapshot_hash):
            raise CitationVerificationError("Citation Corpus snapshot hash is invalid")
        if not re.fullmatch(r"[0-9a-f]{64}", context_hash):
            raise CitationVerificationError("Citation Context hash is invalid")
        if not prompt_version or not retriever_version:
            raise CitationVerificationError("Citation prompt/retriever provenance is missing")
        return VerifiedCitation(
            context_id=context_id,
            feature_id=feature_id,
            alias=alias,
            chunk_id=str(binding["chunk_id"]),
            version_id=str(binding["version_id"]),
            publication_number=str(binding["publication_number"]),
            section_type=str(binding["section_type"]),
            section_label=str(binding["section_label"]),
            claim_number=(
                None if binding.get("claim_number") is None else int(binding["claim_number"])
            ),
            start_offset=start,
            end_offset=end,
            text_hash=str(binding["text_hash"]),
            excerpt=excerpt,
            corpus_snapshot_hash=corpus_snapshot_hash,
            prompt_version=prompt_version,
            retriever_version=retriever_version,
            context_hash=context_hash,
        )


__all__ = [
    "CitationVerificationError",
    "CitationVerifier",
    "ModelCitationSelection",
    "VerifiedCitation",
]
