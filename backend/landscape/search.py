from __future__ import annotations

import asyncio
import re
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import date, timedelta
from enum import StrEnum

from pydantic import Field

from idea.merge import (
    MergedHit,
    merge_hits,
    normalize_application_number,
    normalize_family_id,
    normalize_publication_number,
)
from idea.providers.base import (
    ProviderResult,
    ProviderRunner,
    ProviderStatus,
    SearchHit,
    SearchProvider,
    SearchQuery,
)

from .assignee_matching import (
    AssigneeResolutionStatus,
    resolve_competitor_assignee,
)
from .planning import validate_query_plan_scope
from .schemas import AnalysisMode, LandscapeModel, LandscapeQueryPlan, LandscapeScope
from .workflow import NonRetryableLandscapeWorkflowError


class ExclusionReason(StrEnum):
    INVALID_PUBLICATION_NUMBER = "INVALID_PUBLICATION_NUMBER"
    PUBLICATION_DATE_MISSING = "PUBLICATION_DATE_MISSING"
    PUBLICATION_DATE_INVALID = "PUBLICATION_DATE_INVALID"
    PUBLICATION_DATE_OUTSIDE_WINDOW = "PUBLICATION_DATE_OUTSIDE_WINDOW"
    COMPETITOR_AMBIGUOUS = "COMPETITOR_AMBIGUOUS"
    COMPETITOR_NOT_CONFIRMED = "COMPETITOR_NOT_CONFIRMED"


class LandscapeCandidateLimitExceededError(NonRetryableLandscapeWorkflowError):
    """The complete eligible patent-family set exceeds its configured safety bound."""

    error_code = "LANDSCAPE_CANDIDATE_LIMIT_EXCEEDED"

    def __init__(self, *, unique_candidate_count: int, candidate_limit: int):
        self.unique_candidate_count = unique_candidate_count
        self.candidate_limit = candidate_limit
        super().__init__(
            "eligible deduplicated patent-family count "
            f"{unique_candidate_count} exceeds candidate_limit safety bound "
            f"{candidate_limit}; the candidate set was not truncated"
        )


class CompanyPatentCount(LandscapeModel):
    company: str
    patent_count: int = Field(ge=1)
    share: float = Field(ge=0.0, le=1.0)
    source: str


class LandscapeSearchCoverage(LandscapeModel):
    raw_hit_count: int = Field(ge=0)
    eligible_hit_count: int = Field(ge=0)
    unique_publication_count: int = Field(ge=0)
    unique_family_count: int = Field(ge=0)
    confirmed_family_count: int = Field(default=0, ge=0)
    application_group_count: int = Field(default=0, ge=0)
    publication_fallback_count: int = Field(default=0, ge=0)
    identity_conflict_count: int = Field(default=0, ge=0)
    unique_candidate_count: int = Field(ge=0)
    selected_count: int = Field(ge=0)
    truncated_count: int = Field(ge=0)
    candidate_limit_exceeded: bool = False
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
    family_footprint: int = Field(ge=0)
    family_score: float = Field(ge=0.0, le=1.0)
    rank_quality: float = Field(ge=0.0, le=1.0)
    recency_score: float = Field(ge=0.0, le=1.0)
    query_consensus_bonus: float = Field(ge=0.0, le=0.03)
    provider_consensus_bonus: float = Field(ge=0.0, le=0.02)
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

    unique_publications = {
        normalize_publication_number(hit.publication_number)
        for _, hits in eligible_batches
        for hit in hits
    }
    merged, identity_conflict_count = _merge_hits_by_family(eligible_batches)
    candidate_limit = scope.budget.candidate_limit
    ranked = _rank_candidates(merged, scope, direction_terms or [])
    selected_keys = {item.merge_key for item in merged}
    selected = [item for item, _, _ in ranked]
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
                family_footprint=int(metrics["family_footprint"]),
                family_score=round(metrics["family_score"], 6),
                rank_quality=round(metrics["rank_quality"], 6),
                recency_score=round(metrics["recency_score"], 6),
                query_consensus_bonus=round(
                    metrics["query_consensus_bonus"], 6
                ),
                provider_consensus_bonus=round(
                    metrics["provider_consensus_bonus"], 6
                ),
                selected=item.merge_key in selected_keys,
                reasons=_ranking_reasons(item, metrics),
            )
            for item, score, metrics in ranked
        ],
        coverage=LandscapeSearchCoverage(
            raw_hit_count=raw_count,
            eligible_hit_count=eligible_count,
            unique_publication_count=len(unique_publications),
            unique_family_count=len(merged),
            confirmed_family_count=sum(
                candidate.family_id is not None for candidate in merged
            ),
            application_group_count=sum(
                candidate.family_id is None
                and candidate.application_number is not None
                for candidate in merged
            ),
            publication_fallback_count=sum(
                candidate.family_id is None
                and candidate.application_number is None
                for candidate in merged
            ),
            identity_conflict_count=identity_conflict_count,
            unique_candidate_count=len(merged),
            selected_count=len(selected),
            truncated_count=0,
            candidate_limit_exceeded=len(merged) > candidate_limit,
            excluded_counts=dict(sorted(exclusions.items())),
            provider_statuses=provider_statuses or {},
            company_patent_counts=company_counts,
        ),
    )


def _merge_hits_by_family(
    batches: list[tuple[str, list[SearchHit]]],
) -> tuple[list[MergedHit], int]:
    """Merge publications when a provider exposes a shared family/application identity."""

    records = [
        (query_id, hit)
        for query_id, hits in batches
        for hit in hits
    ]
    families_by_publication: dict[str, set[str]] = defaultdict(set)
    applications_by_publication: dict[str, set[str]] = defaultdict(set)
    families_by_application: dict[str, set[str]] = defaultdict(set)
    for _query_id, hit in records:
        publication = normalize_publication_number(hit.publication_number)
        application = normalize_application_number(hit.application_number)
        family = normalize_family_id(hit.family_id)
        if publication and application:
            applications_by_publication[publication].add(application)
        if publication and family:
            families_by_publication[publication].add(family)
        if application and family:
            families_by_application[application].add(family)
    conflicting_publications = {
        publication
        for publication in set(families_by_publication)
        | set(applications_by_publication)
        if len(families_by_publication[publication]) > 1
        or len(applications_by_publication[publication]) > 1
    }
    conflicting_applications = {
        application
        for application, families in families_by_application.items()
        if len(families) > 1
    }
    conflict_publications = set(conflicting_publications)
    enriched_records = []
    for query_id, hit in records:
        publication = normalize_publication_number(hit.publication_number)
        if publication is None:  # guarded by exclusion_reason; keep this fail closed
            raise ValueError("eligible hit is missing a normalized publication number")
        application = normalize_application_number(hit.application_number)
        identity_conflict = (
            publication in conflicting_publications
            or application in conflicting_applications
        )
        if identity_conflict:
            conflict_publications.add(publication)
        enriched_records.append(
            (
                query_id,
                hit.model_copy(
                    update={
                        "application_number": (
                            None
                            if publication in conflicting_publications
                            else hit.application_number
                        ),
                        "family_id": None if identity_conflict else hit.family_id,
                        "raw": {
                            **hit.raw,
                            "_landscape_assignee_observation": hit.assignee,
                            "_landscape_publication_observation": publication,
                            "_landscape_identity_conflict": identity_conflict,
                        },
                    }
                ),
            )
        )
    enriched_records.sort(key=_family_representative_order)
    merged = merge_hits(
        [(query_id, [hit]) for query_id, hit in enriched_records]
    )
    return merged, len(conflict_publications)


def _family_representative_order(
    record: tuple[str, SearchHit],
) -> tuple[int, str, str, str, int, str]:
    query_id, hit = record
    publication = normalize_publication_number(hit.publication_number) or ""
    kind = re.search(r"([A-Z])\d*$", publication)
    kind_priority = 0 if kind and kind.group(1) == "A" else 1
    return (
        kind_priority,
        publication,
        hit.provider,
        query_id,
        hit.provider_rank,
        hit.url,
    )


def weighted_analysis_selection(
    candidates: list[MergedHit],
    *,
    scope: LandscapeScope,
    company_patent_counts: list[CompanyPatentCount] | None = None,
) -> list[MergedHit]:
    """Guarantee company coverage, then allocate deep-review slots by company weight."""
    if not candidates:
        return []
    original_order = {
        candidate.merge_key: index
        for index, candidate in enumerate(candidates)
    }
    queues: dict[str, list[MergedHit]] = defaultdict(list)
    for candidate in candidates:
        queues[candidate_company(candidate, scope)].append(candidate)
    for items in queues.values():
        items.sort(
            key=lambda item: (
                -family_footprint(item),
                original_order[item.merge_key],
                item.publication_number or item.title,
            )
        )

    weights = {company: len(items) for company, items in queues.items()}
    for item in company_patent_counts or []:
        company = _normalize_text(item.company) or "unknown"
        if company in queues:
            weights[company] = item.patent_count
    company_order = sorted(
        queues,
        key=lambda company: (
            -weights[company],
            -family_footprint(queues[company][0]),
            company.casefold(),
        ),
    )
    target = min(scope.budget.analysis_limit, len(candidates))
    ordered: list[MergedHit] = []
    assigned: Counter[str] = Counter()

    # A prefix can cover every company only when the user supplied enough analysis slots.
    for company in company_order[:target]:
        ordered.append(queues[company].pop(0))
        assigned[company] += 1

    def append_weighted() -> bool:
        available = [company for company in company_order if queues[company]]
        if not available:
            return False
        company = min(
            available,
            key=lambda name: (
                -(weights[name] / (assigned[name] + 1)),
                -family_footprint(queues[name][0]),
                name.casefold(),
            ),
        )
        ordered.append(queues[company].pop(0))
        assigned[company] += 1
        return True

    while len(ordered) < target and append_weighted():
        pass
    while append_weighted():
        pass
    return ordered


def candidate_company(hit: MergedHit, scope: LandscapeScope) -> str:
    return _normalize_text(_company_for_hit(hit, scope)[0]) or "unknown"


def family_footprint(hit: MergedHit) -> int:
    """Count confirmed family jurisdictions exposed by search metadata."""
    jurisdictions: set[str] = set()
    for source in hit.sources:
        status = source.raw.get("country_status")
        if isinstance(status, dict):
            jurisdictions.update(str(value).upper() for value in status if value)
        elif isinstance(status, list):
            jurisdictions.update(str(value).upper() for value in status if value)
    for publication in family_publication_numbers(hit):
        match = re.match(r"^([A-Z]{2})", publication)
        if match:
            jurisdictions.add(match.group(1))
    if jurisdictions:
        return len(jurisdictions)
    return 1 if hit.family_id else 0


def family_score(footprint: int) -> float:
    """Score verified jurisdiction breadth without linearly rewarding duplicates."""

    if footprint <= 0:
        return 0.0
    if footprint == 1:
        return 0.2
    if footprint == 2:
        return 0.4
    if footprint <= 4:
        return 0.6
    if footprint <= 7:
        return 0.8
    return 1.0


def family_publication_numbers(hit: MergedHit) -> list[str]:
    values = [
        normalize_publication_number(
            str(source.raw.get("_landscape_publication_observation") or "")
        )
        for source in hit.sources
    ]
    if hit.publication_number:
        values.append(normalize_publication_number(hit.publication_number))
    return sorted({value for value in values if value})


def matched_competitor_name(
    assignee: str | None, scope: LandscapeScope
) -> str | None:
    resolution = resolve_competitor_assignee(assignee, scope.competitors)
    if resolution.is_confirmed and resolution.competitor is not None:
        return resolution.competitor.name
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
    if scope.mode in {AnalysisMode.COMPETITOR, AnalysisMode.TECHNOLOGY_COMPETITOR}:
        resolution = resolve_competitor_assignee(hit.assignee, scope.competitors)
        if resolution.status == AssigneeResolutionStatus.AMBIGUOUS:
            return ExclusionReason.COMPETITOR_AMBIGUOUS
        if not resolution.is_confirmed:
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
    max_queries = max((len(hit.query_ids) for hit in hits), default=1)
    max_providers = max((len(hit.found_by) for hit in hits), default=1)
    ranked: list[tuple[MergedHit, float, dict[str, float]]] = []
    terms = _search_terms(direction_terms or [scope.technology_direction or ""])
    for hit, rrf_score in zip(hits, raw_rrf, strict=True):
        technical = _technical_relevance(hit, terms)
        footprint = family_footprint(hit)
        rank_quality = _rank_quality(hit, scope.budget.per_query_limit)
        recency = _recency_score(hit, scope)
        query_bonus = (
            0.03 * (len(hit.query_ids) - 1) / (max_queries - 1)
            if max_queries > 1
            else 0.0
        )
        provider_bonus = (
            0.02 * (len(hit.found_by) - 1) / (max_providers - 1)
            if max_providers > 1
            else 0.0
        )
        metrics = {
            "rrf_score": rrf_score,
            "technical_relevance": technical,
            "family_footprint": float(footprint),
            "family_score": family_score(footprint),
            "rank_quality": rank_quality,
            "recency_score": recency,
            "query_consensus_bonus": query_bonus,
            "provider_consensus_bonus": provider_bonus,
        }
        score = _mode_score(scope.mode, metrics) + query_bonus + provider_bonus
        ranked.append((hit, min(1.0, score), metrics))
    return sorted(
        ranked,
        key=lambda item: (
            -item[1],
            item[0].publication_number or item[0].title,
        ),
    )


def _mode_score(mode: AnalysisMode, metrics: dict[str, float]) -> float:
    if mode == AnalysisMode.COMPETITOR:
        return (
            0.55 * metrics["family_score"]
            + 0.30 * metrics["rank_quality"]
            + 0.15 * metrics["recency_score"]
        )
    if mode == AnalysisMode.TECHNOLOGY_COMPETITOR:
        return (
            0.45 * metrics["technical_relevance"]
            + 0.35 * metrics["family_score"]
            + 0.15 * metrics["rank_quality"]
            + 0.05 * metrics["recency_score"]
        )
    return (
        0.55 * metrics["technical_relevance"]
        + 0.25 * metrics["family_score"]
        + 0.15 * metrics["rank_quality"]
        + 0.05 * metrics["recency_score"]
    )


def _rank_quality(hit: MergedHit, per_query_limit: int) -> float:
    best_rank = min(source.provider_rank for source in hit.sources)
    if per_query_limit <= 1:
        return 1.0
    bounded_rank = min(max(best_rank, 1), per_query_limit)
    return 1.0 - (bounded_rank - 1) / (per_query_limit - 1)


def _recency_score(hit: MergedHit, scope: LandscapeScope) -> float:
    if not hit.publication_date:
        return 0.0
    try:
        published = date.fromisoformat(hit.publication_date[:10])
    except (TypeError, ValueError):
        return 0.0
    window_days = (scope.publication_end - scope.publication_start).days
    if window_days <= 0:
        return 1.0
    elapsed = (published - scope.publication_start).days
    return min(1.0, max(0.0, elapsed / window_days))


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
    reasons.append(f"可核验同族法域 {int(metrics['family_footprint'])} 个")
    reasons.append(f"同族布局分 {metrics['family_score']:.2f}")
    reasons.append(f"固定排名分 {metrics['rank_quality']:.2f}")
    reasons.append(f"时间活跃度 {metrics['recency_score']:.2f}")
    if metrics["query_consensus_bonus"] > 0:
        reasons.append(
            f"跨检索式确认 +{metrics['query_consensus_bonus']:.3f}"
        )
    if metrics["provider_consensus_bonus"] > 0:
        reasons.append(
            f"跨 Provider 确认 +{metrics['provider_consensus_bonus']:.3f}"
        )
    return reasons
