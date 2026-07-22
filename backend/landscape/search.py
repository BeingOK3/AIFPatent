from __future__ import annotations

import asyncio
import re
import time
import unicodedata
from collections import Counter
from datetime import date, timedelta
from enum import StrEnum

from pydantic import Field

from idea.merge import MergedHit, merge_hits, normalize_publication_number
from idea.providers.base import (
    ProviderResult,
    ProviderRunner,
    ProviderStatus,
    SearchHit,
    SearchProvider,
    SearchQuery,
)

from .planning import validate_query_plan_scope
from .schemas import AnalysisMode, LandscapeModel, LandscapeQueryPlan, LandscapeScope


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


def scoped_provider_query_text(
    query_text: str, scope: LandscapeScope, provider: str = "google_patents_local"
) -> str:
    """Build provider-specific hints; post-filtering remains the authoritative date gate."""
    if provider == "exa_mcp":
        return (
            f"{query_text} patent published from {scope.publication_start.isoformat()} "
            f"to {scope.publication_end.isoformat()}"
        )
    after = (scope.publication_start - timedelta(days=1)).strftime("%Y%m%d")
    before = (scope.publication_end + timedelta(days=1)).strftime("%Y%m%d")
    return f"{query_text} after=publication:{after} before=publication:{before}"


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
    if scope.mode in {AnalysisMode.COMPETITOR, AnalysisMode.TECHNOLOGY_COMPETITOR} and not assignee_matches_confirmed_competitor(
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
    results = await execute_provider_queries(
        scope=scope,
        plan=plan,
        providers=providers,
        runner=runner,
        timeout_seconds=timeout_seconds,
    )
    batches: list[tuple[str, list[SearchHit]]] = []
    provider_statuses: dict[str, str] = {}
    for result in results:
        provider_statuses[f"{result.request_id}:{result.provider}"] = result.status.value
        if result.succeeded:
            batches.append((result.request_id, result.hits))
    return (
        strict_filter_and_select(
            batches,
            scope=scope,
            provider_statuses=provider_statuses,
        ),
        results,
    )


async def execute_provider_queries(
    *,
    scope: LandscapeScope,
    plan: LandscapeQueryPlan,
    providers: list[SearchProvider],
    runner: ProviderRunner | None = None,
    timeout_seconds: float | dict[str, float] = 30.0,
) -> list[ProviderResult]:
    validate_query_plan_scope(plan, scope)
    runner = runner or ProviderRunner()
    semaphores = {provider.name: asyncio.Semaphore(1) for provider in providers}
    circuits: dict[str, tuple[str, str] | None] = {provider.name: None for provider in providers}
    last_call_at: dict[str, float] = {}
    rate_limited_until: dict[str, float] = {}

    def provider_timeout(name: str) -> float:
        if isinstance(timeout_seconds, dict):
            return timeout_seconds.get(name, 30.0)
        return timeout_seconds

    async def call(provider: SearchProvider, query: SearchQuery) -> ProviderResult:
        async with semaphores[provider.name]:
            circuit = circuits[provider.name]
            if circuit is not None:
                return ProviderResult(
                    provider=provider.name,
                    operation="search",
                    request_id=query.query_id,
                    status=ProviderStatus.DISABLED,
                    duration_ms=0,
                    error_code=circuit[0],
                    error_message=circuit[1],
                )
            if provider.name == "exa_mcp":
                delay = max(
                    0.0,
                    last_call_at.get(provider.name, 0.0) + 1.0 - time.monotonic(),
                    rate_limited_until.get(provider.name, 0.0) - time.monotonic(),
                )
                if delay:
                    await asyncio.sleep(delay)
            result = await runner.search(
                provider, query, timeout_seconds=provider_timeout(provider.name)
            )
            last_call_at[provider.name] = time.monotonic()
            if result.status == ProviderStatus.TIMEOUT and provider.name == "google_patents_local":
                circuits[provider.name] = (
                    "PROVIDER_CIRCUIT_OPEN",
                    "Google Patents timed out; remaining queries skipped for this run",
                )
            if result.error_message and "429 Too Many Requests" in result.error_message:
                rate_limited_until[provider.name] = time.monotonic() + 10.0
            return result

    calls = []
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
            provider_query = query.model_copy(
                update={"text": scoped_provider_query_text(planned.query_text, scope, provider.name)}
            )
            calls.append(call(provider, provider_query))
    return list(await asyncio.gather(*calls)) if calls else []


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"[^\w\u3400-\u9fff]+", " ", normalized)
    return " ".join(normalized.split())


def _contains_cjk(value: str) -> bool:
    return any("\u3400" <= character <= "\u9fff" for character in value)
