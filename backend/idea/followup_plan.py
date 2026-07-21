from __future__ import annotations

from enum import Enum
from typing import Iterable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .followup import FollowupError, FollowupMode, FollowupTurn
from .hybrid import QuestionType
from .merge import normalize_publication_number


class FollowupPlanModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FollowupQuestionType(str, Enum):
    CLAIM_OVERLAP = "CLAIM_OVERLAP"
    TECHNICAL_EXPLANATION = "TECHNICAL_EXPLANATION"
    NOVELTY = "NOVELTY"
    DESIGN_AROUND = "DESIGN_AROUND"
    GENERAL = "GENERAL"

    def as_retrieval_type(self) -> QuestionType:
        return QuestionType(self.value)


class FollowupPlan(FollowupPlanModel):
    mode: FollowupMode
    question_type: FollowupQuestionType
    selected_publication_numbers: list[str] = Field(min_length=1)
    query_rewrites: list[str] = Field(min_length=1, max_length=5)
    preferred_sections: list[str] = Field(default_factory=list)
    required_features: list[str] = Field(default_factory=list)
    requires_new_research: bool = False
    requires_legal_review: bool = False
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def normalized_unique_values(self) -> "FollowupPlan":
        publications = []
        for value in self.selected_publication_numbers:
            normalized = normalize_publication_number(value)
            if normalized is None:
                raise ValueError("follow-up plan contains an invalid publication number")
            publications.append(normalized)
        if len(set(publications)) != len(publications):
            raise ValueError("follow-up plan publication numbers must be unique")
        queries = [" ".join(value.split()) for value in self.query_rewrites]
        if any(not value for value in queries) or len(set(queries)) != len(queries):
            raise ValueError("follow-up plan query rewrites must be non-empty and unique")
        allowed_sections = {"abstract", "claims", "description"}
        if len(set(self.preferred_sections)) != len(self.preferred_sections) or any(
            value not in allowed_sections for value in self.preferred_sections
        ):
            raise ValueError("follow-up plan preferred sections are invalid or duplicated")
        if len(set(self.required_features)) != len(self.required_features):
            raise ValueError("follow-up plan Feature IDs must be unique")
        self.selected_publication_numbers = publications
        self.query_rewrites = queries
        return self


class FollowupPlanVerifier:
    def verify(
        self,
        plan: FollowupPlan,
        *,
        turn: FollowupTurn,
        allowed_feature_ids: Iterable[str],
    ) -> FollowupPlan:
        if plan.mode != turn.mode:
            raise FollowupError("follow-up plan mode does not match the frozen Turn")
        allowed_publications = set(turn.scope.publication_numbers)
        unknown_publications = set(plan.selected_publication_numbers) - allowed_publications
        if unknown_publications:
            raise FollowupError(
                "follow-up plan selected publications outside the Turn scope: "
                + ", ".join(sorted(unknown_publications))
            )
        allowed_features = set(allowed_feature_ids)
        unknown_features = set(plan.required_features) - allowed_features
        if unknown_features:
            raise FollowupError(
                "follow-up plan references unknown IDEA Features: "
                + ", ".join(sorted(unknown_features))
            )
        if plan.question_type == FollowupQuestionType.DESIGN_AROUND and (
            plan.mode != FollowupMode.DESIGN_AROUND
        ):
            raise FollowupError("design-around question type requires DESIGN_AROUND mode")
        if plan.mode == FollowupMode.NEW_RESEARCH and not plan.requires_new_research:
            raise FollowupError("NEW_RESEARCH mode must set requires_new_research")
        return plan


__all__ = [
    "FollowupPlan",
    "FollowupPlanVerifier",
    "FollowupQuestionType",
]
