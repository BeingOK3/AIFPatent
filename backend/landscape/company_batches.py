from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from .company_assignment import CompanyAssignmentResult
from .schemas import (
    CompanyAssignment,
    LandscapePatentAnalysis,
    NormalizedCompany,
)


class CompanyBatchValidationError(ValueError):
    """Raised when analyzed patents cannot form an exact company partition."""


@dataclass(frozen=True)
class CompanyAnalysisItem:
    publication_number: str
    assignment: CompanyAssignment
    analysis: LandscapePatentAnalysis


@dataclass(frozen=True)
class CompanyAnalysisBatch:
    company: NormalizedCompany
    items: tuple[CompanyAnalysisItem, ...]

    @property
    def company_id(self) -> str:
        return self.company.company_id

    @property
    def publication_numbers(self) -> tuple[str, ...]:
        return tuple(item.publication_number for item in self.items)


def build_company_analysis_batches(
    assignment_result: CompanyAssignmentResult,
    analyses: Mapping[str, LandscapePatentAnalysis],
) -> tuple[CompanyAnalysisBatch, ...]:
    """Partition every successful patent analysis by its PRIMARY company."""

    companies = {
        company.company_id: company for company in assignment_result.companies
    }
    if len(companies) != len(assignment_result.companies):
        raise CompanyBatchValidationError("company IDs must be unique")

    assignments: dict[str, CompanyAssignment] = {}
    for assignment in assignment_result.assignments:
        publication = assignment.publication_number
        if publication in assignments:
            raise CompanyBatchValidationError(
                f"duplicate primary assignment: {publication}"
            )
        if assignment.primary_company_id not in companies:
            raise CompanyBatchValidationError(
                f"assignment references unknown company: {assignment.primary_company_id}"
            )
        assignments[publication] = assignment

    unknown_analyses = sorted(set(analyses) - set(assignments))
    if unknown_analyses:
        raise CompanyBatchValidationError(
            "analyses outside company assignment set: "
            + ", ".join(unknown_analyses)
        )
    for publication, analysis in analyses.items():
        if analysis.publication_number != publication:
            raise CompanyBatchValidationError(
                f"analysis publication identity mismatch: {publication}"
            )

    items_by_company: dict[str, list[CompanyAnalysisItem]] = {}
    for publication in sorted(analyses):
        assignment = assignments[publication]
        items_by_company.setdefault(
            assignment.primary_company_id, []
        ).append(
            CompanyAnalysisItem(
                publication_number=publication,
                assignment=assignment,
                analysis=analyses[publication],
            )
        )

    batches = tuple(
        CompanyAnalysisBatch(
            company=companies[company_id],
            items=tuple(items_by_company[company_id]),
        )
        for company_id in sorted(items_by_company)
    )
    validate_company_analysis_batches(
        batches,
        expected_publications=set(analyses),
    )
    return batches


def validate_company_analysis_batches(
    batches: Sequence[CompanyAnalysisBatch],
    *,
    expected_publications: set[str],
) -> None:
    company_ids = [batch.company_id for batch in batches]
    if company_ids != sorted(company_ids) or len(company_ids) != len(set(company_ids)):
        raise CompanyBatchValidationError(
            "company batches must have unique, stable company order"
        )

    actual: list[str] = []
    for batch in batches:
        publications = list(batch.publication_numbers)
        if not publications:
            raise CompanyBatchValidationError("company batches cannot be empty")
        if publications != sorted(publications):
            raise CompanyBatchValidationError(
                f"company batch is not stably ordered: {batch.company_id}"
            )
        for item in batch.items:
            if item.assignment.primary_company_id != batch.company_id:
                raise CompanyBatchValidationError(
                    f"item assigned to wrong company batch: {item.publication_number}"
                )
            if item.analysis.publication_number != item.publication_number:
                raise CompanyBatchValidationError(
                    f"analysis identity mismatch: {item.publication_number}"
                )
        actual.extend(publications)

    duplicates = sorted(
        publication for publication in set(actual) if actual.count(publication) > 1
    )
    missing = sorted(expected_publications - set(actual))
    invented = sorted(set(actual) - expected_publications)
    if duplicates or missing or invented:
        raise CompanyBatchValidationError(
            "company batches must cover analyzed patents exactly "
            f"(duplicates={duplicates}, missing={missing}, outside_a={invented})"
        )


__all__ = [
    "CompanyAnalysisBatch",
    "CompanyAnalysisItem",
    "CompanyBatchValidationError",
    "build_company_analysis_batches",
    "validate_company_analysis_batches",
]
