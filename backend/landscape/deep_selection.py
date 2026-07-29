from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Mapping

from .schemas import LandscapeDirectionFingerprint


@dataclass(frozen=True)
class DeepSelection:
    publication_number: str
    company_id: str
    score: float
    rank: int
    reason_codes: tuple[str, ...]
    time_bucket: str


def select_deep_patents(
    fingerprints: Mapping[str, LandscapeDirectionFingerprint],
    *,
    ranking: Mapping[str, Mapping[str, float]] | None = None,
    limit: int = 20,
    minimum_per_company: int = 1,
    minimum_per_direction: int = 1,
    minimum_per_time_bucket: int = 1,
    max_per_company_ratio: float = 0.5,
    period_bucket: str = "QUARTER",
) -> list[DeepSelection]:
    """Select representative patents deterministically for optional deep review."""
    if limit <= 0 or not fingerprints:
        return []
    scores = {
        publication: _score(
            fingerprint,
            (ranking or {}).get(publication, {}),
        )
        for publication, fingerprint in fingerprints.items()
    }
    ordered = sorted(
        fingerprints,
        key=lambda publication: (-scores[publication], publication),
    )
    selected: list[str] = []
    reasons: dict[str, set[str]] = {}
    company_counts: dict[str, int] = {}
    direction_counts: dict[str, int] = {}
    bucket_counts: dict[str, int] = {}

    def add(publication: str, reason: str) -> bool:
        if publication in selected or len(selected) >= limit:
            return False
        item = fingerprints[publication]
        company_limit = max(1, int(limit * max_per_company_ratio))
        if company_counts.get(item.company_id, 0) >= company_limit:
            return False
        selected.append(publication)
        reasons.setdefault(publication, set()).add(reason)
        company_counts[item.company_id] = company_counts.get(item.company_id, 0) + 1
        direction = _direction(item)
        direction_counts[direction] = direction_counts.get(direction, 0) + 1
        bucket = _bucket(item.publication_date, period_bucket)
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1
        return True

    for company in sorted({item.company_id for item in fingerprints.values()}):
        for publication in ordered:
            if fingerprints[publication].company_id == company and add(
                publication, "COMPANY_COVERAGE"
            ):
                break
    for direction in sorted({_direction(item) for item in fingerprints.values()}):
        for publication in ordered:
            if _direction(fingerprints[publication]) == direction and add(
                publication, "DIRECTION_COVERAGE"
            ):
                break
    for bucket in sorted({_bucket(item.publication_date, period_bucket) for item in fingerprints.values()}):
        for publication in ordered:
            if _bucket(fingerprints[publication].publication_date, period_bucket) == bucket and add(
                publication, "TIME_BUCKET_COVERAGE"
            ):
                break
    for publication in ordered:
        add(publication, "HIGH_COMPOSITE_SCORE")
    return [
        DeepSelection(
            publication_number=publication,
            company_id=fingerprints[publication].company_id,
            score=round(scores[publication], 6),
            rank=index,
            reason_codes=tuple(sorted(reasons.get(publication, {"HIGH_COMPOSITE_SCORE"}))),
            time_bucket=_bucket(fingerprints[publication].publication_date, period_bucket),
        )
        for index, publication in enumerate(selected, start=1)
    ]


def _score(
    fingerprint: LandscapeDirectionFingerprint,
    rank: Mapping[str, float],
) -> float:
    # The single-provider case treats provider coverage as a neutral full score.
    return round(
        0.35 * float(rank.get("technical_relevance", 0.5))
        + 0.20 * float(rank.get("query_consensus", 0.0))
        + 0.15 * float(rank.get("direction_representativeness", 0.5))
        + 0.10 * float(rank.get("time_representativeness", 0.5))
        + 0.10 * min(1.0, len(fingerprint.evidence) / 3)
        + 0.10 * float(rank.get("recency", 0.5)),
        6,
    )


def _direction(item: LandscapeDirectionFingerprint) -> str:
    return item.technical_keywords[0] if item.technical_keywords else "UNKNOWN_DIRECTION"


def _bucket(value: str | None, period: str) -> str:
    if not value:
        return "UNKNOWN_TIME"
    parsed = date.fromisoformat(value[:10])
    if period == "MONTH":
        return f"{parsed.year:04d}-{parsed.month:02d}"
    quarter = (parsed.month - 1) // 3 + 1
    return f"{parsed.year:04d}-Q{quarter}"


__all__ = ["DeepSelection", "select_deep_patents"]
