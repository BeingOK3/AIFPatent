from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence

from .company_assignment import CompanyAssignmentResult
from .schemas import (
    CompanyTechnologyProfile,
    CrossCompanyTrendAnalysis,
    LandscapeCoverageAudit,
    LandscapePatentAnalysis,
    LandscapeDirectionFingerprint,
)


class LandscapeCoverageAuditError(ValueError):
    """Raised when program-owned audit inputs are internally contradictory."""


def audit_company_trend_coverage(
    *,
    eligible_publications: Sequence[str],
    assignment_result: CompanyAssignmentResult,
    fetched_publications: set[str],
    analyses: Mapping[str, LandscapePatentAnalysis],
    profiles: Mapping[str, CompanyTechnologyProfile],
    trends: CrossCompanyTrendAnalysis | None,
    valid_evidence_ids_by_publication: Mapping[str, set[str]],
    lightweight_fingerprints: Mapping[str, LandscapeDirectionFingerprint] | None = None,
    repair_round: int = 0,
    max_repair_rounds: int = 1,
) -> LandscapeCoverageAudit:
    """Compute U/F/A/T coverage and evidence integrity without model judgment."""

    if repair_round < 0 or max_repair_rounds < 0:
        raise ValueError("repair rounds cannot be negative")
    eligible_list = list(eligible_publications)
    if len(eligible_list) != len(set(eligible_list)):
        raise LandscapeCoverageAuditError(
            "eligible publication set U must be unique"
        )
    eligible = set(eligible_list)

    assignment_publications = [
        assignment.publication_number
        for assignment in assignment_result.assignments
    ]
    assignment_counts = Counter(assignment_publications)
    assignment_duplicates = {
        publication
        for publication, count in assignment_counts.items()
        if count > 1
    }
    assignment_by_publication = {
        assignment.publication_number: assignment.primary_company_id
        for assignment in assignment_result.assignments
    }
    assignment_company_ids = {
        company.company_id for company in assignment_result.companies
    }
    unknown_assignment_companies = {
        assignment.primary_company_id
        for assignment in assignment_result.assignments
        if assignment.primary_company_id not in assignment_company_ids
    }

    analyzed = set(analyses)
    lightweight = set(lightweight_fingerprints or {})
    trend_input = lightweight or analyzed
    classified_members: list[str] = []
    wrong_company_members: set[str] = set()
    invalid_evidence: set[str] = set()
    evidence_gap_targets: set[str] = set()
    for company_id, profile in profiles.items():
        for category in profile.technology_categories:
            classified_members.extend(category.publication_numbers)
            for publication in category.publication_numbers:
                if assignment_by_publication.get(publication) != company_id:
                    wrong_company_members.add(publication)
            allowed = set().union(
                *(
                    valid_evidence_ids_by_publication.get(publication, set())
                    for publication in category.publication_numbers
                )
            )
            cited = set(category.evidence_ids)
            category_invalid = cited - allowed
            invalid_evidence.update(category_invalid)
            if category_invalid:
                evidence_gap_targets.add(f"PROFILE:{company_id}")
            if any(
                not (
                    cited
                    & valid_evidence_ids_by_publication.get(publication, set())
                )
                for publication in category.publication_numbers
            ):
                evidence_gap_targets.add(f"PROFILE:{company_id}")

    classified_counts = Counter(classified_members)
    classified = set(classified_members)
    duplicate_memberships = {
        publication
        for publication, count in classified_counts.items()
        if count > 1
    }
    duplicate_memberships.update(assignment_duplicates)
    duplicate_memberships.update(wrong_company_members)

    trend_publications: set[str] = set()
    if trends is not None:
        for trend in trends.trends:
            publications = set(trend.publication_numbers)
            trend_publications.update(publications)
            expected_companies = {
                assignment_by_publication.get(publication)
                for publication in publications
            }
            if None in expected_companies or set(trend.company_ids) != expected_companies:
                evidence_gap_targets.add(f"TREND:{trend.trend_id}")
            allowed = set().union(
                *(
                    valid_evidence_ids_by_publication.get(publication, set())
                    for publication in publications
                )
            )
            cited = set(trend.evidence_ids)
            trend_invalid = cited - allowed
            invalid_evidence.update(trend_invalid)
            if trend_invalid:
                evidence_gap_targets.add(f"TREND:{trend.trend_id}")
            if any(
                not (
                    cited
                    & valid_evidence_ids_by_publication.get(publication, set())
                )
                for publication in publications
            ):
                evidence_gap_targets.add(f"TREND:{trend.trend_id}")

    all_publication_references = (
        set(assignment_publications)
        | fetched_publications
        | analyzed
        | classified
        | trend_publications
        | lightweight
    )
    invented = all_publication_references - eligible
    missing = eligible - classified
    coverage_ratio = (
        len(eligible & classified) / len(eligible)
        if eligible
        else 1.0
    )

    repair_targets: set[str] = set(evidence_gap_targets)
    repair_targets.update(
        f"FETCH:{publication}"
        for publication in eligible - fetched_publications
    )
    if lightweight_fingerprints is None:
        repair_targets.update(
            f"ANALYZE:{publication}"
            for publication in (eligible & fetched_publications) - analyzed
        )
    else:
        repair_targets.update(
            f"LIGHTWEIGHT:{publication}"
            for publication in (eligible & fetched_publications) - lightweight
        )
    repair_targets.update(
        f"CLASSIFY:{publication}"
        for publication in (eligible & analyzed) - classified
    )
    repair_targets.update(
        f"CLASSIFY:{publication}" for publication in duplicate_memberships
    )
    if invalid_evidence:
        repair_targets.update(evidence_gap_targets)

    fatal_assignment_corruption = (
        set(assignment_publications) != eligible
        or bool(assignment_duplicates)
        or bool(unknown_assignment_companies)
    )
    set_order_corruption = (
        not fetched_publications <= eligible
        or not analyzed <= fetched_publications
        or not classified <= trend_input
        or not set(valid_evidence_ids_by_publication) <= trend_input
    )
    limitations: list[str] = []
    if fatal_assignment_corruption:
        limitations.append("公司 PRIMARY 归属未形成完整且唯一的 U 分区。")
    if wrong_company_members:
        limitations.append("公司分类包含归属于其他公司的专利。")
    if set_order_corruption:
        limitations.append(
            "持久化集合不满足 T ⊆ L ⊆ F ⊆ U。"
            if lightweight_fingerprints is not None
            else "持久化集合不满足 T ⊆ A ⊆ F ⊆ U。"
        )
    if invalid_evidence:
        limitations.append("公司分析或趋势引用了无效或跨专利 Evidence。")

    if invented or fatal_assignment_corruption or set_order_corruption:
        decision = "FAIL"
        repair_targets = set()
    elif (
        duplicate_memberships
        or missing
        or invalid_evidence
        or evidence_gap_targets
    ):
        if repair_targets and repair_round < max_repair_rounds:
            decision = "REPAIR"
        else:
            decision = "LIMITED"
            repair_targets = set()
            limitations.append("覆盖缺口已达到修复轮次上限。")
    else:
        decision = "PASS"

    return LandscapeCoverageAudit(
        decision=decision,
        coverage_ratio=round(coverage_ratio, 6),
        invented_publications=sorted(invented),
        duplicate_memberships=sorted(duplicate_memberships),
        missing_publications=sorted(missing),
        invalid_evidence_refs=sorted(invalid_evidence),
        repair_targets=sorted(repair_targets),
        limitations=limitations,
    )


__all__ = [
    "LandscapeCoverageAuditError",
    "audit_company_trend_coverage",
]
