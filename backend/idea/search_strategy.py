from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Iterable, Literal

from .config import SearchMode, SearchSettings
from .merge import MergedHit, normalize_publication_number

DEFAULT_RELEVANCE_THRESHOLD = 0.15


class ScopeBreadth(StrEnum):
    NARROW = "narrow"
    MEDIUM = "medium"
    BROAD = "broad"


class StopReason(StrEnum):
    SATURATED = "SATURATED"
    CANDIDATE_MAX = "CANDIDATE_MAX"
    QUERY_EXHAUSTED = "QUERY_EXHAUSTED"
    PROVIDERS_UNAVAILABLE = "PROVIDERS_UNAVAILABLE"


@dataclass(frozen=True)
class SearchBudget:
    breadth: ScopeBreadth
    candidate_max: int
    deep_review_min: int
    deep_review_max: int
    deep_review_target: int
    per_query_limit: int


@dataclass(frozen=True)
class RoundStats:
    round_number: int
    total_candidates: int
    new_families: int
    new_high_relevance_families: int
    successful_providers: int


@dataclass
class SaturationTracker:
    consecutive_rounds: int
    max_new_high_relevance_families: int
    candidate_max: int
    history: list[RoundStats] = field(default_factory=list)

    def add(self, stats: RoundStats) -> StopReason | None:
        if self.history and stats.round_number <= self.history[-1].round_number:
            raise ValueError("round numbers must increase")
        self.history.append(stats)
        if stats.total_candidates >= self.candidate_max:
            return StopReason.CANDIDATE_MAX
        if stats.successful_providers == 0:
            return StopReason.PROVIDERS_UNAVAILABLE
        recent = self.history[-self.consecutive_rounds :]
        if len(recent) == self.consecutive_rounds and all(
            item.new_high_relevance_families <= self.max_new_high_relevance_families
            for item in recent
        ):
            return StopReason.SATURATED
        return None


@dataclass(frozen=True)
class ScreenedCandidate:
    hit: MergedHit
    relevance_score: float
    matched_terms: tuple[str, ...]
    date_status: Literal["ELIGIBLE", "AFTER_EVALUATION_DATE", "UNKNOWN"]


@dataclass(frozen=True)
class DeepReviewSelection:
    selected: tuple[ScreenedCandidate, ...]
    target: int
    minimum: int
    limitation: dict | None


def assess_breadth(
    *,
    technical_domains: Iterable[str],
    features: Iterable[str],
    search_terms: Iterable[str],
    explicit: str | None = None,
) -> ScopeBreadth:
    if explicit in {item.value for item in ScopeBreadth}:
        return ScopeBreadth(explicit)
    domains = [item for item in technical_domains if item.strip()]
    feature_list = [item for item in features if item.strip()]
    terms = [item.strip() for item in search_terms if item.strip()]
    specific_terms = sum(1 for term in terms if len(_tokens(term)) >= 2 or len(term) >= 10)
    score = 0
    if len(domains) >= 3:
        score += 2
    elif len(domains) == 2:
        score += 1
    if len(feature_list) <= 2:
        score += 2
    elif len(feature_list) <= 4:
        score += 1
    if not terms or specific_terms / max(1, len(terms)) < 0.4:
        score += 2
    elif specific_terms / len(terms) < 0.7:
        score += 1
    if score >= 5:
        return ScopeBreadth.BROAD
    if score >= 2:
        return ScopeBreadth.MEDIUM
    return ScopeBreadth.NARROW


def build_budget(
    settings: SearchSettings,
    breadth: ScopeBreadth,
    *,
    mode_name: str | None = None,
    candidate_max: int | None = None,
    deep_review_min: int | None = None,
    deep_review_max: int | None = None,
) -> SearchBudget:
    mode = settings.mode(mode_name)
    customized = SearchMode(
        candidate_max=candidate_max if candidate_max is not None else mode.candidate_max,
        deep_review_min=deep_review_min if deep_review_min is not None else mode.deep_review_min,
        deep_review_max=deep_review_max if deep_review_max is not None else mode.deep_review_max,
    )
    ratio = {
        ScopeBreadth.NARROW: 0.0,
        ScopeBreadth.MEDIUM: 0.5,
        ScopeBreadth.BROAD: 1.0,
    }[breadth]
    target = round(
        customized.deep_review_min
        + ratio * (customized.deep_review_max - customized.deep_review_min)
    )
    per_query = min(20, max(10, math.ceil(customized.candidate_max / (4 if breadth == ScopeBreadth.BROAD else 3))))
    return SearchBudget(
        breadth=breadth,
        candidate_max=customized.candidate_max,
        deep_review_min=customized.deep_review_min,
        deep_review_max=customized.deep_review_max,
        deep_review_target=target,
        per_query_limit=per_query,
    )


def screen_summaries(
    hits: Iterable[MergedHit],
    *,
    idea_terms: Iterable[str],
    evaluation_date: date,
    term_groups: Iterable[Iterable[str]] | None = None,
) -> list[ScreenedCandidate]:
    normalized_terms = {_normalize_term(term) for term in idea_terms if _normalize_term(term)}
    normalized_groups = [
        {_normalize_term(term) for term in group if _normalize_term(term)}
        for group in (term_groups or [])
    ]
    normalized_groups = [group for group in normalized_groups if group]
    screened = []
    for hit in hits:
        date_status: Literal["ELIGIBLE", "AFTER_EVALUATION_DATE", "UNKNOWN"] = "UNKNOWN"
        if hit.publication_date:
            try:
                published = date.fromisoformat(hit.publication_date[:10])
                date_status = (
                    "ELIGIBLE" if published <= evaluation_date else "AFTER_EVALUATION_DATE"
                )
            except ValueError:
                date_status = "UNKNOWN"
        searchable = _normalize_term(f"{hit.title} {hit.snippet}")
        matched = tuple(sorted(term for term in normalized_terms if term in searchable))
        title_matches = sum(1 for term in matched if term in _normalize_term(hit.title))
        if normalized_groups:
            group_matches = sum(
                1 for group in normalized_groups if any(term in searchable for term in group)
            )
            score = min(
                1.0,
                group_matches / len(normalized_groups) * 0.7
                + min(0.2, len(matched) * 0.05)
                + min(0.1, title_matches * 0.05),
            )
        else:
            coverage = len(matched) / max(1, len(normalized_terms))
            score = min(1.0, coverage * 0.75 + min(0.25, title_matches * 0.08))
        screened.append(
            ScreenedCandidate(
                hit=hit,
                relevance_score=round(score, 4),
                matched_terms=matched,
                date_status=date_status,
            )
        )
    return sorted(
        screened,
        key=lambda item: (
            item.date_status == "AFTER_EVALUATION_DATE",
            -item.relevance_score,
            item.hit.publication_number or item.hit.title,
        ),
    )


def select_deep_review(
    screened: Iterable[ScreenedCandidate],
    budget: SearchBudget,
    *,
    relevance_threshold: float = DEFAULT_RELEVANCE_THRESHOLD,
) -> DeepReviewSelection:
    eligible = [
        item
        for item in screened
        if item.date_status != "AFTER_EVALUATION_DATE"
        and item.relevance_score >= relevance_threshold
        and normalize_publication_number(item.hit.publication_number) is not None
    ]
    selected = tuple(eligible[: budget.deep_review_target])
    limitation = None
    if len(selected) < budget.deep_review_min:
        limitation = {
            "code": "INSUFFICIENT_RELEVANT_DEEP_REVIEWS",
            "required": budget.deep_review_min,
            "selected": len(selected),
            "message": "相关文献数量不足；系统未使用弱相关文献凑足深读数量。",
        }
    return DeepReviewSelection(
        selected=selected,
        target=budget.deep_review_target,
        minimum=budget.deep_review_min,
        limitation=limitation,
    )


def _tokens(value: str) -> list[str]:
    return re.findall(r"[\w\u4e00-\u9fff]+", value, flags=re.UNICODE)


def _normalize_term(value: str) -> str:
    return " ".join(_tokens(value.lower()))
