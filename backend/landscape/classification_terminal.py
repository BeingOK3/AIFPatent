from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from .scope import ScopeModel
from .taxonomy import TaxonomyArtifact


class ClassificationAction(StrEnum):
    EXACT_CATEGORY = "EXACT_CATEGORY"
    NONE_OF_CANDIDATES = "NONE_OF_CANDIDATES"
    NEEDS_ALTERNATIVE_PARENT = "NEEDS_ALTERNATIVE_PARENT"
    UNRESOLVED = "UNRESOLVED"


class ClassificationTerminal(StrEnum):
    CLASSIFIED = "CLASSIFIED"
    OTHERS = "OTHERS"
    UNRESOLVED = "UNRESOLVED"


class ClassificationResult(ScopeModel):
    analysis_unit_id: str = Field(pattern=r"^AU-[0-9a-f]{16}$")
    action: ClassificationAction
    terminal: ClassificationTerminal
    primary_category_id: str | None = None
    auxiliary_category_ids: tuple[str, ...] = Field(default=(), max_length=5)
    evidence_ids: tuple[str, ...] = Field(default=(), max_length=50)
    confidence: float = Field(ge=0, le=1)
    review_round: int = Field(ge=0, le=1)
    unresolved_reason: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def validate_terminal_shape(self) -> "ClassificationResult":
        if self.terminal == ClassificationTerminal.CLASSIFIED:
            if self.action != ClassificationAction.EXACT_CATEGORY or not self.primary_category_id:
                raise ValueError("classified terminal requires exact primary category")
            if self.unresolved_reason:
                raise ValueError("classified terminal cannot be unresolved")
        elif self.terminal == ClassificationTerminal.OTHERS:
            if self.primary_category_id or self.action == ClassificationAction.UNRESOLVED:
                raise ValueError("Others cannot have primary category or unresolved action")
        else:
            if self.primary_category_id or not self.unresolved_reason:
                raise ValueError("unresolved terminal requires reason and no category")
        if len(self.auxiliary_category_ids) != len(set(self.auxiliary_category_ids)):
            raise ValueError("duplicate auxiliary category")
        return self


def validate_classification(
    result: ClassificationResult,
    *,
    expected_analysis_unit_id: str,
    allowed_evidence_ids: set[str],
    taxonomy: TaxonomyArtifact,
) -> ClassificationResult:
    if result.analysis_unit_id != expected_analysis_unit_id:
        raise ValueError("classification belongs to another analysis unit")
    if not set(result.evidence_ids).issubset(allowed_evidence_ids):
        raise ValueError("classification cites unknown evidence")
    leaves = set(taxonomy.leaf_category_ids)
    categories = ({result.primary_category_id} if result.primary_category_id else set()) | set(result.auxiliary_category_ids)
    if not categories.issubset(leaves):
        raise ValueError("classification cites unknown or non-leaf category")
    if result.primary_category_id in set(result.auxiliary_category_ids):
        raise ValueError("primary category cannot also be auxiliary")
    return result


def reconcile_terminals(
    frozen_analysis_unit_ids: tuple[str, ...],
    results: tuple[ClassificationResult, ...],
) -> dict[ClassificationTerminal, tuple[str, ...]]:
    frozen = set(frozen_analysis_unit_ids)
    if len(frozen) != len(frozen_analysis_unit_ids):
        raise ValueError("frozen analysis units contain duplicates")
    result_ids = [result.analysis_unit_id for result in results]
    if len(result_ids) != len(set(result_ids)):
        raise ValueError("classification results contain duplicates")
    if set(result_ids) != frozen:
        missing = sorted(frozen - set(result_ids))
        extra = sorted(set(result_ids) - frozen)
        raise ValueError(f"classification reconciliation mismatch: missing={missing}, extra={extra}")
    return {
        terminal: tuple(sorted(result.analysis_unit_id for result in results if result.terminal == terminal))
        for terminal in ClassificationTerminal
    }


__all__ = ["ClassificationAction", "ClassificationResult", "ClassificationTerminal", "reconcile_terminals", "validate_classification"]
