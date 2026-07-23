from __future__ import annotations

import asyncio
import re
import time
import unicodedata
from collections import Counter, defaultdict
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


class CompanyPatentCount(LandscapeModel):
    company: str
    patent_count: int = Field(ge=1)
    share: float = Field(ge=0.0, le=1.0)
    source: str


class LandscapeSearchCoverage(LandscapeModel):
    raw_hit_count: int = Field(ge=0)
    eligible_hit_count: int = Field(ge=0)
    unique_candidate_count: int = Field(ge=0)
    selected_count: int = Field(ge=0)
    truncated_count: int = Field(ge=0)
    excluded_counts: dict[str, int]
    provider_statuses: dict[str, str] = Field(default_factory=dict)
    company_patent_counts: list[CompanyPatentCount] = Field(default_factory=list)


class LandscapeCandidateRank(LandscapeModel):
    publication_number: str
    company: str
    score: float = Field(ge=0.0, le=1.0)
    rrf_score: float = Field(ge=0.0)
    query_coverage: int = Field(ge=1)
    provider_coverage: int = Field(ge=1)
    technical_relevance: float = Field(ge=0.0, le=1.0)
    selected: bool
    reasons: list[str]


class LandscapeSearchResult(LandscapeModel):
    candidates: list[MergedHit]
    coverage: LandscapeSearchCoverage
    ranking: list[LandscapeCandidateRank] = Field(default_factory=list)


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
    direction_terms: list[str] | None = None,
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
    ranked = _rank_candidates(merged, scope, direction_terms or [])
    selected_ranked = _balanced_candidate_selection(
        ranked, scope, min(candidate_limit, len(ranked))
    )
    selected_keys = {item[0].merge_key for item in selected_ranked}
    selected = [item[0] for item in selected_ranked]
    company_counts = _company_patent_counts(merged, scope)
    return LandscapeSearchResult(
        candidates=selected,
        ranking=[
            LandscapeCandidateRank(
                publication_number=item.publication_number or item.merge_key,
                company=_company_for_hit(item, scope)[0],
                score=round(score, 6),
                rrf_score=round(metrics["rrf_score"], 8),
                query_coverage=len(item.query_ids),
                provider_coverage=len(item.found_by),
                technical_relevance=round(metrics["technical_relevance"], 6),
                selected=item.merge_key in selected_keys,
                reasons=_ranking_reasons(item, metrics),
            )
            for item, score, metrics in ranked
        ],
        coverage=LandscapeSearchCoverage(
            raw_hit_count=raw_count,
            eligible_hit_count=eligible_count,
            unique_candidate_count=len(merged),
            selected_count=len(selected),
            truncated_count=max(0, len(merged) - len(selected)),
            excluded_counts=dict(sorted(exclusions.items())),
            provider_statuses=provider_statuses or {},
            company_patent_counts=company_counts,
        ),
    )


def balanced_analysis_selection(
    candidates: list[MergedHit],
    *,
    scope: LandscapeScope,
) -> list[MergedHit]:
    """Return a deterministic company-round-robin order for deep review and backfill."""
    queues: dict[str, list[MergedHit]] = defaultdict(list)
    company_order: list[str] = []
    for candidate in candidates:
        company, _ = _company_for_hit(candidate, scope)
        if company not in queues:
            company_order.append(company)
        queues[company].append(candidate)
    ordered: list[MergedHit] = []
    while any(queues.values()):
        for company in company_order:
            if queues[company]:
                ordered.append(queues[company].pop(0))
    return ordered


def matched_competitor_name(
    assignee: str | None, scope: LandscapeScope
) -> str | None:
    if not assignee:
        return None
    normalized_assignee = _normalize_text(assignee)
    for competitor in scope.competitors:
        for alias in [competitor.name, *competitor.aliases]:
            normalized_alias = _normalize_text(alias)
            if not normalized_alias:
                continue
            if _contains_cjk(normalized_alias):
                if normalized_alias in normalized_assignee:
                    return competitor.name
            elif re.search(
                rf"(?<![a-z0-9]){re.escape(normalized_alias)}(?![a-z0-9])",
                normalized_assignee,
            ):
                return competitor.name
    return None


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
    return matched_competitor_name(assignee, scope) is not None


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
            direction_terms=plan.direction_terms,
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
            if provider.name == "serpapi_google_patents" and result.error_code in {
                "SERPAPI_RATE_LIMITED",
                "SERPAPI_API_KEY_REQUIRED",
                "SERPAPI_AUTH_ERROR",
            }:
                circuits[provider.name] = (
                    result.error_code,
                    result.error_message or "SerpAPI provider disabled for this run",
                )
            elif (
                provider.name == "exa_mcp"
                and result.error_message
                and "429 Too Many Requests" in result.error_message
            ):
                circuits[provider.name] = (
                    "EXA_RATE_LIMITED",
                    "Exa anonymous quota or rate limit reached; remaining queries skipped",
                )
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


def _company_for_hit(hit: MergedHit, scope: LandscapeScope) -> tuple[str, str]:
    competitor = matched_competitor_name(hit.assignee, scope)
    if competitor:
        return competitor, "CONFIRMED_COMPETITOR"
    assignee = " ".join((hit.assignee or "").split())
    return (assignee or "未知权利人", "PROVIDER_ASSIGNEE" if assignee else "MISSING")


def _company_patent_counts(
    hits: list[MergedHit], scope: LandscapeScope
) -> list[CompanyPatentCount]:
    grouped: dict[str, dict[str, object]] = {}
    for hit in hits:
        label, source = _company_for_hit(hit, scope)
        key = _normalize_text(label) or "unknown"
        current = grouped.setdefault(
            key, {"label": label, "source": source, "count": 0}
        )
        if len(label) > len(str(current["label"])):
            current["label"] = label
        current["count"] = int(current["count"]) + 1
    total = len(hits)
    return [
        CompanyPatentCount(
            company=str(value["label"]),
            patent_count=int(value["count"]),
            share=round(int(value["count"]) / total, 6) if total else 0.0,
            source=str(value["source"]),
        )
        for value in sorted(
            grouped.values(),
            key=lambda item: (-int(item["count"]), str(item["label"]).casefold()),
        )
    ]


def _rank_candidates(
    hits: list[MergedHit],
    scope: LandscapeScope,
    direction_terms: list[str],
) -> list[tuple[MergedHit, float, dict[str, float]]]:
    if not hits:
        return []
    raw_rrf = [
        sum(1.0 / (60.0 + source.provider_rank) for source in hit.sources)
        for hit in hits
    ]
    max_rrf = max(raw_rrf, default=1.0)
    max_queries = max((len(hit.query_ids) for hit in hits), default=1)
    max_providers = max((len(hit.found_by) for hit in hits), default=1)
    ranked: list[tuple[MergedHit, float, dict[str, float]]] = []
    terms = _search_terms(direction_terms or [scope.technology_direction or ""])
    for hit, rrf_score in zip(hits, raw_rrf, strict=True):
        technical = _technical_relevance(hit, terms)
        metrics = {
            "rrf_score": rrf_score,
            "rrf_normalized": rrf_score / max_rrf if max_rrf else 0.0,
            "query_normalized": len(hit.query_ids) / max_queries,
            "provider_normalized": len(hit.found_by) / max_providers,
            "technical_relevance": technical,
        }
        score = (
            0.5 * metrics["rrf_normalized"]
            + 0.2 * metrics["query_normalized"]
            + 0.1 * metrics["provider_normalized"]
            + 0.2 * technical
        )
        ranked.append((hit, min(1.0, score), metrics))
    return sorted(
        ranked,
        key=lambda item: (
            -item[1],
            item[0].publication_number or item[0].title,
        ),
    )


def _balanced_candidate_selection(
    ranked: list[tuple[MergedHit, float, dict[str, float]]],
    scope: LandscapeScope,
    limit: int,
) -> list[tuple[MergedHit, float, dict[str, float]]]:
    if limit >= len(ranked):
        return ranked
    queues: dict[str, list[tuple[MergedHit, float, dict[str, float]]]] = defaultdict(list)
    for item in ranked:
        queues[_company_for_hit(item[0], scope)[0]].append(item)
    if scope.mode in {AnalysisMode.COMPETITOR, AnalysisMode.TECHNOLOGY_COMPETITOR}:
        companies = [
            competitor.name for competitor in scope.competitors if queues[competitor.name]
        ]
        total = sum(len(queues[name]) for name in companies)
        equal_slots = int(limit * 0.4)
        proportional_slots = limit - equal_slots
        quotas = {
            name: min(
                len(queues[name]),
                equal_slots // max(1, len(companies))
                + round(proportional_slots * len(queues[name]) / max(1, total)),
            )
            for name in companies
        }
    else:
        cap = max(1, (limit + 3) // 4)
        quotas = {name: min(len(items), cap) for name, items in queues.items()}
    selected: list[tuple[MergedHit, float, dict[str, float]]] = []
    selected_keys: set[str] = set()
    for item in ranked:
        company = _company_for_hit(item[0], scope)[0]
        if quotas.get(company, 0) <= 0:
            continue
        selected.append(item)
        selected_keys.add(item[0].merge_key)
        quotas[company] -= 1
        if len(selected) == limit:
            break
    if len(selected) < limit:
        for item in ranked:
            if item[0].merge_key in selected_keys:
                continue
            selected.append(item)
            selected_keys.add(item[0].merge_key)
            if len(selected) == limit:
                break
    return sorted(
        selected,
        key=lambda item: (-item[1], item[0].publication_number or item[0].title),
    )


def _search_terms(values: list[str]) -> set[str]:
    terms: set[str] = set()
    for value in values:
        normalized = _normalize_text(value)
        if normalized:
            terms.add(normalized)
            terms.update(
                token for token in normalized.split() if len(token) > 1
            )
    return terms


def _technical_relevance(hit: MergedHit, terms: set[str]) -> float:
    if not terms:
        return 0.0
    title = _normalize_text(hit.title)
    text = _normalize_text(f"{hit.title} {hit.snippet}")
    matched = sum(1 for term in terms if term in text)
    title_matched = sum(1 for term in terms if term in title)
    return min(1.0, matched / len(terms) * 0.75 + title_matched / len(terms) * 0.25)


def _ranking_reasons(
    hit: MergedHit, metrics: dict[str, float]
) -> list[str]:
    reasons = [
        f"命中 {len(hit.query_ids)} 个检索式",
        f"来自 {len(hit.found_by)} 个 Provider",
        f"最佳原始排名 {min(source.provider_rank for source in hit.sources)}",
    ]
    if metrics["technical_relevance"] > 0:
        reasons.append(
            f"技术文本匹配 {metrics['technical_relevance']:.2f}"
        )
    return reasons
