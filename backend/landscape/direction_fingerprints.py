from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable

from idea.merge import MergedHit
from idea.providers.base import FetchedDocument

from .schemas import LandscapeDirectionEvidence, LandscapeDirectionFingerprint


_TOKEN_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9-]{2,}|[\u3400-\u9fff]{2,}")


def build_direction_fingerprint(
    hit: MergedHit,
    *,
    company_id: str,
    document: FetchedDocument | None = None,
    max_text_per_section: int = 2_000,
) -> LandscapeDirectionFingerprint:
    """Build a bounded, deterministic technology signal for one eligible patent.

    Search metadata is sufficient for a fallback fingerprint. When details are
    available, only the abstract, the beginning of claims, and a short
    description excerpt are included. This function never invokes an LLM.
    """
    evidence: list[LandscapeDirectionEvidence] = []
    title = (document.title if document else hit.title) or ""
    _append_evidence(evidence, "TITLE", title, hit.publication_number)
    if document is not None:
        _append_evidence(
            evidence,
            "ABSTRACT",
            document.abstract_text,
            hit.publication_number,
            max_text_per_section,
        )
        _append_evidence(
            evidence,
            "CLAIM",
            document.claims_text,
            hit.publication_number,
            max_text_per_section,
        )
        _append_evidence(
            evidence,
            "DESCRIPTION",
            document.description_text,
            hit.publication_number,
            max_text_per_section // 2,
        )
    else:
        _append_evidence(
            evidence,
            "SNIPPET",
            hit.snippet,
            hit.publication_number,
            max_text_per_section,
        )
    if not evidence:
        _append_evidence(
            evidence,
            "SNIPPET",
            hit.snippet or hit.title or hit.publication_number,
            hit.publication_number,
            max_text_per_section,
        )
    corpus = " ".join(item.text for item in evidence)
    keywords = _keywords(corpus)
    return LandscapeDirectionFingerprint(
        publication_number=hit.publication_number or "",
        company_id=company_id,
        title=title[:1_000],
        publication_date=(
            document.publication_date if document else hit.publication_date
        ),
        source_kind="FETCHED_DOCUMENT" if document is not None else "SEARCH_HIT",
        technical_keywords=keywords,
        evidence=evidence,
    )


def build_direction_fingerprints(
    hits: Iterable[MergedHit],
    *,
    company_by_publication: dict[str, str],
    documents: dict[str, FetchedDocument] | None = None,
) -> list[LandscapeDirectionFingerprint]:
    """Build a stable fingerprint for every eligible publication."""
    documents = documents or {}
    fingerprints = [
        build_direction_fingerprint(
            hit,
            company_id=company_by_publication[hit.publication_number or ""],
            document=documents.get(hit.publication_number or ""),
        )
        for hit in hits
    ]
    return sorted(fingerprints, key=lambda item: item.publication_number)


def _append_evidence(
    output: list[LandscapeDirectionEvidence],
    section_type: str,
    value: str,
    publication_number: str | None,
    limit: int = 2_000,
) -> None:
    text = " ".join((value or "").split())[: max(1, limit)]
    if not text:
        return
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    evidence_id = "EV-DIR-" + hashlib.sha256(
        f"{publication_number or ''}|{section_type}|{digest}".encode("utf-8")
    ).hexdigest()[:24]
    output.append(
        LandscapeDirectionEvidence(
            evidence_id=evidence_id,
            section_type=section_type,
            text=text,
            content_hash=digest,
        )
    )


def _keywords(text: str) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for token in _TOKEN_PATTERN.findall(text):
        normalized = token.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(token)
        if len(result) >= 30:
            break
    return result


__all__ = ["build_direction_fingerprint", "build_direction_fingerprints"]
