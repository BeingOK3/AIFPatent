from __future__ import annotations

from enum import StrEnum
from datetime import date

from pydantic import Field

from .query_planning import V4QueryPlan
from .scope import ScopeModel


class ScaleGateError(ValueError):
    pass


class ScaleTier(StrEnum):
    WITHIN_DEFAULT = "WITHIN_DEFAULT"
    CONFIRM_MEDIUM = "CONFIRM_MEDIUM"
    CONFIRM_LARGE = "CONFIRM_LARGE"


class ScaleEstimate(ScopeModel):
    scope_revision_id: str = Field(pattern=r"^SCR-[0-9a-f]{16}$")
    scope_revision_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    publication_start: date
    publication_end: date
    query_count: int = Field(ge=1, le=100_000)
    estimated_total_results: int = Field(ge=0)
    estimated_total_pages: int = Field(ge=0)
    estimated_shards: int = Field(ge=0)
    tier: ScaleTier
    stop_reason: str | None = Field(default=None, max_length=200)


def estimate_scale(
    plan: V4QueryPlan,
    reported_totals: tuple[int, ...],
    *,
    page_size: int = 100,
    shard_size: int = 200,
) -> ScaleEstimate:
    if len(reported_totals) != len(plan.queries):
        raise ScaleGateError("one provider total is required for every planned query")
    if not 1 <= page_size <= 100:
        raise ScaleGateError("page_size must be between 1 and 100")
    if not 1 <= shard_size <= 10_000:
        raise ScaleGateError("shard_size must be between 1 and 10000")
    if any(not isinstance(total, int) or isinstance(total, bool) or total < 0 for total in reported_totals):
        raise ScaleGateError("reported totals must be non-negative integers")
    total = sum(reported_totals)
    pages = sum((value + page_size - 1) // page_size for value in reported_totals)
    shards = (total + shard_size - 1) // shard_size if total else 0
    tier = (
        ScaleTier.WITHIN_DEFAULT
        if total <= 500
        else ScaleTier.CONFIRM_MEDIUM
        if total <= 1000
        else ScaleTier.CONFIRM_LARGE
    )
    return ScaleEstimate(
        **plan_scope_fields(plan),
        query_count=len(plan.queries),
        estimated_total_results=total,
        estimated_total_pages=pages,
        estimated_shards=shards,
        tier=tier,
    )


def requires_confirmation(estimate: ScaleEstimate) -> bool:
    return estimate.tier != ScaleTier.WITHIN_DEFAULT


def plan_scope_fields(plan: V4QueryPlan) -> dict[str, object]:
    query = plan.queries[0]
    return {
        "scope_revision_id": plan.scope_revision_id,
        "scope_revision_hash": plan.scope_revision_hash,
        "publication_start": query.publication_start,
        "publication_end": query.publication_end,
    }


__all__ = ["ScaleEstimate", "ScaleGateError", "ScaleTier", "estimate_scale", "requires_confirmation"]
