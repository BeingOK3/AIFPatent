from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from .abstract_evidence import AbstractEvidence, AbstractStatus
from .scope import ScopeModel
from .taxonomy import TaxonomyArtifact


class DirectionStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNRESOLVED = "UNRESOLVED"


class DirectionRecord(ScopeModel):
    analysis_unit_id: str = Field(pattern=r"^AU-[0-9a-f]{16}$")
    status: DirectionStatus
    evidence_sufficient: bool
    technical_problem: str = Field(default="", max_length=1000)
    solution_mechanism: str = Field(default="", max_length=1500)
    technical_object: str = Field(default="", max_length=1000)
    application_scenarios: tuple[str, ...] = Field(default=(), max_length=12)
    direction_summary: str = Field(default="", max_length=1500)
    keywords: tuple[str, ...] = Field(default=(), max_length=20)
    candidate_level1_ids: tuple[str, ...] = Field(default=(), max_length=5)
    confidence: float = Field(ge=0, le=1)
    evidence_ids: tuple[str, ...] = Field(default=(), max_length=50)
    unresolved_reason: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def validate_terminal(self) -> "DirectionRecord":
        if self.status == DirectionStatus.AVAILABLE:
            if not self.evidence_sufficient or not self.solution_mechanism.strip() or not self.direction_summary.strip():
                raise ValueError("available direction requires sufficient mechanism and summary")
            if not self.evidence_ids or self.unresolved_reason:
                raise ValueError("available direction requires evidence and no unresolved reason")
        else:
            if self.evidence_sufficient or not self.unresolved_reason:
                raise ValueError("unresolved direction requires explicit reason")
            if self.evidence_ids:
                raise ValueError("unresolved direction cannot cite evidence")
        return self


def validate_direction_record(
    record: DirectionRecord,
    *,
    expected_analysis_unit_id: str,
    allowed_evidence_ids: set[str],
    taxonomy: TaxonomyArtifact,
) -> DirectionRecord:
    if record.analysis_unit_id != expected_analysis_unit_id:
        raise ValueError("direction record belongs to another analysis unit")
    if not set(record.evidence_ids).issubset(allowed_evidence_ids):
        raise ValueError("direction record cites unknown evidence")
    level1_ids = {node.category_id for node in taxonomy.nodes if node.level == 1}
    if not set(record.candidate_level1_ids).issubset(level1_ids):
        raise ValueError("direction record cites unknown taxonomy parent")
    if len(record.evidence_ids) != len(set(record.evidence_ids)):
        raise ValueError("direction record repeats evidence")
    return record


def unresolved_from_abstract(
    analysis_unit_id: str,
    abstract: AbstractEvidence,
) -> DirectionRecord | None:
    if abstract.status == AbstractStatus.AVAILABLE:
        return None
    return DirectionRecord(
        analysis_unit_id=analysis_unit_id,
        status=DirectionStatus.UNRESOLVED,
        evidence_sufficient=False,
        confidence=0,
        unresolved_reason=abstract.unresolved_reason or abstract.status.value,
    )


__all__ = ["DirectionRecord", "DirectionStatus", "unresolved_from_abstract", "validate_direction_record"]
