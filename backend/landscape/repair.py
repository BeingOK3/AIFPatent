from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .company_assignment import CompanyAssignmentResult
from .schemas import LandscapeCoverageAudit


class LandscapeRepairPlanError(ValueError):
    """Raised when an audit target would escape the frozen run scope."""


@dataclass(frozen=True)
class LandscapeRepairPlan:
    """Program-owned, bounded work derived from one immutable audit snapshot."""

    fetch_publications: tuple[str, ...]
    analyze_publications: tuple[str, ...]
    rebuild_company_ids: tuple[str, ...]
    rebuild_cross_company_trends: bool


def build_repair_plan(
    audit: LandscapeCoverageAudit,
    *,
    eligible_publications: Iterable[str],
    assignment_result: CompanyAssignmentResult,
) -> LandscapeRepairPlan:
    """Resolve only audited targets; never rediscover or expand a search scope."""

    if audit.decision != "REPAIR":
        raise LandscapeRepairPlanError(
            f"repair plan requires REPAIR audit, got {audit.decision}"
        )
    eligible_values = tuple(eligible_publications)
    eligible = set(eligible_values)
    if not eligible:
        raise LandscapeRepairPlanError("repair plan requires a non-empty eligible set")
    if len(eligible) != len(eligible_values):
        raise LandscapeRepairPlanError("eligible publications must be unique")
    assignment_by_publication = {
        assignment.publication_number: assignment.primary_company_id
        for assignment in assignment_result.assignments
    }
    company_ids = {company.company_id for company in assignment_result.companies}
    if set(assignment_by_publication) != eligible:
        raise LandscapeRepairPlanError("assignment result must partition eligible publications")
    if not set(assignment_by_publication.values()) <= company_ids:
        raise LandscapeRepairPlanError("assignment refers to an unknown company")

    fetch: set[str] = set()
    analyze: set[str] = set()
    rebuild_companies: set[str] = set()
    trend_targeted = False

    for target in audit.repair_targets:
        prefix, separator, subject = target.partition(":")
        if not separator or not subject:
            raise LandscapeRepairPlanError(f"malformed repair target: {target}")
        if prefix in {"FETCH", "ANALYZE", "CLASSIFY"}:
            if subject not in eligible:
                raise LandscapeRepairPlanError(
                    f"repair target outside eligible set: {target}"
                )
            if prefix == "FETCH":
                fetch.add(subject)
                analyze.add(subject)
            elif prefix == "ANALYZE":
                analyze.add(subject)
            rebuild_companies.add(assignment_by_publication[subject])
        elif prefix == "PROFILE":
            if subject not in company_ids:
                raise LandscapeRepairPlanError(
                    f"repair profile target has unknown company: {target}"
                )
            rebuild_companies.add(subject)
        elif prefix == "TREND":
            trend_targeted = True
        else:
            raise LandscapeRepairPlanError(f"unsupported repair target: {target}")

    return LandscapeRepairPlan(
        fetch_publications=tuple(sorted(fetch)),
        analyze_publications=tuple(sorted(analyze)),
        rebuild_company_ids=tuple(sorted(rebuild_companies)),
        rebuild_cross_company_trends=bool(rebuild_companies) or trend_targeted,
    )


__all__ = [
    "LandscapeRepairPlan",
    "LandscapeRepairPlanError",
    "build_repair_plan",
]
