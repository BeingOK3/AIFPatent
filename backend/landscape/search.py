from __future__ import annotations

import asyncio
import re
import unicodedata
from collections import Counter
from datetime import date
from enum import StrEnum

from pydantic import Field

from idea.merge import MergedHit, merge_hits, normalize_publication_number
from idea.providers.base import (
    ProviderResult,
    ProviderRunner,
    SearchHit,
    SearchProvider,
    SearchQuery,
)

from .planning import validate_query_plan_scope
from .schemas import LandscapeModel, LandscapeQueryPlan, LandscapeScope


class ExclusionReason(StrEnum):
    INVALID_PUBLICATION_NUMBER = "INVALID_PUBLICATION_NUMBER"
    PUBLICATION_DATE_MISSING = "PUBLICATION_DATE_MISSING"
    PUBLICATION_DATE_INVALID = "PUBLICATION_DATE_INVALID"
    PUBLICATION_DATE_OUTSIDE_WINDOW = "PUBLICATION_DATE_OUTSIDE_WINDOW"
    COMPETITOR_NOT_CONFIRMED = "COMPETITOR_NOT_CONFIRMED"


class LandscapeSearchCoverage(LandscapeModel):
    raw_hit_count: int = Field(ge=0)
    eligible_hit_count: int = Field(ge=0)
    unique_candidate_count: int = Field(ge=0)
    selected_count: int = Field(ge=0)
    truncated_count: int = Field(ge=0)
    excluded_counts: dict[str, int]
    provider_statuses: dict[str, str] = Field(default_factory=dict)


class LandscapeSearchResult(LandscapeModel):
    candidates: list[MergedHit]
    coverage: LandscapeSearchCoverage


def strict_filter_and_select(
    batches: list[tuple[str, list[SearchHit]]],
    *,
    scope: LandscapeScope,
    provider_statuses: dict[str, str] | None = None,
) -> LandscapeSearchResult:
    """Apply hard publication-date and confirmed-assignee filters before deduplication."""
    eligible_batches: list[tuple[str, list[SearchHit]]] = []
    exclusions: Counter[str] = Counter()
    raw_count = 0
    eligible_count = 0
    for query_id, hits in batches:
        eligible_hits = []
        for hit in hits:
            raw_count += 1
            reason = exclusion_reason(hit, scope)
            if reason is not None:
                exclusions[reason.value] += 1
                continue
            eligible_hits.append(hit)
            eligible_count += 1
        eligible_batches.append((query_id, eligible_hits))

    merged = merge_hits(eligible_batches)
    candidate_limit = scope.budget.candidate_limit
    selected = merged[:candidate_limit]
    return LandscapeSearchResult(
        candidates=selected,
        coverage=LandscapeSearchCoverage(
            raw_hit_count=raw_count,
            eligible_hit_count=eligible_count,
            unique_candidate_count=len(merged),
            selected_count=len(selected),
            truncated_count=max(0, len(merged) - len(selected)),
            excluded_counts=dict(sorted(exclusions.items())),
            provider_statuses=provider_statuses or {},
        ),
    )


def exclusion_reason(hit: SearchHit, scope: LandscapeScope) -> ExclusionReason | None:
    if normalize_publication_number(hit.publication_number) is None:
        return ExclusionReason.INVALID_PUBLICATION_NUMBER
    if not hit.publication_date:
        return ExclusionReason.PUBLICATION_DATE_MISSING
    try:
        publication_date = date.fromisoformat(hit.publication_date[:10])
    except (TypeError, ValueError):
        return ExclusionReason.PUBLICATION_DATE_INVALID
    if not scope.publication_start <= publication_date <= scope.publication_end:
        return ExclusionReason.PUBLICATION_DATE_OUTSIDE_WINDOW
    if scope.mode.value == "COMPETITOR" and not assignee_matches_confirmed_competitor(
        hit.assignee, scope
    ):
        return ExclusionReason.COMPETITOR_NOT_CONFIRMED
    return None


def assignee_matches_confirmed_competitor(
    assignee: str | None, scope: LandscapeScope
) -> bool:
    if not assignee:
        return False
    normalized_assignee = _normalize_text(assignee)
    for competitor in scope.competitors:
        for alias in [competitor.name, *competitor.aliases]:
            normalized_alias = _normalize_text(alias)
            if not normalized_alias:
                continue
            if _contains_cjk(normalized_alias):
                if normalized_alias in normalized_assignee:
                    return True
            elif re.search(
                rf"(?<![a-z0-9]){re.escape(normalized_alias)}(?![a-z0-9])",
                normalized_assignee,
            ):
                return True
    return False


async def execute_search(
    *,
    scope: LandscapeScope,
    plan: LandscapeQueryPlan,
    providers: list[SearchProvider],
    runner: ProviderRunner | None = None,
    timeout_seconds: float = 30.0,
) -> tuple[LandscapeSearchResult, list[ProviderResult]]:
    validate_query_plan_scope(plan, scope)
    runner = runner or ProviderRunner()
    calls = []
    identities: list[tuple[str, str]] = []
    for query_index, planned in enumerate(plan.queries, start=1):
        query_id = f"LQ-{query_index}"
        query = SearchQuery(
            query_id=query_id,
            text=planned.query_text,
            language=planned.language,
            round_number=1,
            limit=scope.budget.per_query_limit,
            query_type="technical_means",
            material_types=["patent"],
        )
        for provider in providers:
            identities.append((query_id, provider.name))
            calls.append(runner.search(provider, query, timeout_seconds=timeout_seconds))
    results = list(await asyncio.gather(*calls)) if calls else []
    batches: list[tuple[str, list[SearchHit]]] = []
    provider_statuses: dict[str, str] = {}
    for (query_id, provider_name), result in zip(identities, results, strict=True):
        provider_statuses[f"{query_id}:{provider_name}"] = result.status.value
        if result.succeeded:
            batches.append((query_id, result.hits))
    return (
        strict_filter_and_select(
            batches,
            scope=scope,
            provider_statuses=provider_statuses,
        ),
        results,
    )


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"[^\w\u3400-\u9fff]+", " ", normalized)
    return " ".join(normalized.split())


def _contains_cjk(value: str) -> bool:
    return any("\u3400" <= character <= "\u9fff" for character in value)
