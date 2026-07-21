from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .context import AssembledModelContext
from .followup import FollowupCitationDraft, FollowupError
from .merge import normalize_publication_number


_CITATION_ALIAS = re.compile(r"^C[1-9][0-9]*$")
_PUBLICATION = re.compile(
    r"\b(?:CN|US|EP|WO|JP|KR|DE|FR|GB|AU|CA)[- ]?[A-Z0-9][A-Z0-9./-]{4,}\b",
    re.I,
)
_FORBIDDEN_LEGAL_CONCLUSIONS = (
    "确定侵权",
    "构成侵权",
    "不构成侵权",
    "保证不侵权",
    "一定不侵权",
    "已经规避专利",
    "definitely infringes",
    "does not infringe",
    "guaranteed non-infringement",
)


class StrictAnswerModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FollowupAnswerType(str, Enum):
    DIRECT = "DIRECT"
    OVERLAP_ANALYSIS = "OVERLAP_ANALYSIS"
    DESIGN_AROUND = "DESIGN_AROUND"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    NEW_RESEARCH_REQUIRED = "NEW_RESEARCH_REQUIRED"


class OverlapLevel(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNCERTAIN = "UNCERTAIN"


class OverlapItem(StrictAnswerModel):
    feature_id: str = Field(pattern=r"^F[1-9][0-9]*$")
    idea_feature: str = Field(min_length=1)
    patent_element: str = Field(min_length=1)
    overlap_level: OverlapLevel
    analysis: str = Field(min_length=1)
    citation_aliases: list[str] = Field(default_factory=list)


class DesignAroundOption(StrictAnswerModel):
    title: str = Field(min_length=1)
    change: str = Field(min_length=1)
    target_features: list[str]
    expected_effect: str = Field(min_length=1)
    engineering_tradeoffs: list[str]
    remaining_risks: list[str]
    citation_aliases: list[str] = Field(default_factory=list)
    requires_new_search: bool


class FollowupAnswer(StrictAnswerModel):
    answer_type: FollowupAnswerType
    direct_answer: str = Field(min_length=1)
    citation_aliases: list[str] = Field(default_factory=list)
    overlap_items: list[OverlapItem] = Field(default_factory=list)
    differences: list[str] = Field(default_factory=list)
    design_around_options: list[DesignAroundOption] = Field(default_factory=list)
    legal_boundary: str = Field(min_length=1)
    limitations: list[str] = Field(default_factory=list)
    needs_new_research: bool = False

    @model_validator(mode="after")
    def aliases_and_answer_type_are_consistent(self) -> "FollowupAnswer":
        alias_lists = [self.citation_aliases]
        alias_lists.extend(item.citation_aliases for item in self.overlap_items)
        alias_lists.extend(item.citation_aliases for item in self.design_around_options)
        for aliases in alias_lists:
            if len(set(aliases)) != len(aliases) or any(
                _CITATION_ALIAS.fullmatch(value) is None for value in aliases
            ):
                raise ValueError("Citation aliases must be unique C1..Cn values")
        if self.answer_type == FollowupAnswerType.INSUFFICIENT_EVIDENCE and (
            self.overlap_items or self.design_around_options
        ):
            raise ValueError("insufficient-evidence answer cannot assert overlap or design options")
        if self.answer_type == FollowupAnswerType.NEW_RESEARCH_REQUIRED:
            self.needs_new_research = True
        return self


@dataclass(frozen=True)
class VerifiedFollowupAnswer:
    answer: FollowupAnswer
    citations: tuple[FollowupCitationDraft, ...]
    used_aliases: tuple[str, ...]


class FollowupAnswerVerifier:
    """Verify model JSON only against one frozen Context citation packet."""

    def verify(
        self,
        answer: FollowupAnswer,
        *,
        context: AssembledModelContext,
        allowed_feature_ids: Iterable[str],
        allowed_publication_numbers: Iterable[str],
    ) -> VerifiedFollowupAnswer:
        if context.purpose != "FOLLOWUP" or not context.turn_id:
            raise FollowupError("follow-up answer requires a FOLLOWUP Context")
        bindings = {
            str(item["alias"]): item for item in context.selected_chunks
        }
        if len(bindings) != len(context.selected_chunks):
            raise FollowupError("Context Citation aliases are not unique")
        allowed_features = set(allowed_feature_ids)
        unknown_features = {
            item.feature_id for item in answer.overlap_items
            if item.feature_id not in allowed_features
        }
        unknown_features.update(
            feature
            for option in answer.design_around_options
            for feature in option.target_features
            if feature not in allowed_features
        )
        if unknown_features:
            raise FollowupError(
                "answer references unknown IDEA features: "
                + ", ".join(sorted(unknown_features))
            )

        citations: list[FollowupCitationDraft] = []
        used_aliases: list[str] = []
        self._append_aliases(
            answer.citation_aliases,
            "direct_answer",
            bindings,
            citations,
            used_aliases,
        )
        for index, item in enumerate(answer.overlap_items):
            if item.overlap_level == OverlapLevel.HIGH and not item.citation_aliases:
                raise FollowupError("HIGH technical overlap requires at least one Citation")
            self._append_aliases(
                item.citation_aliases,
                f"overlap_items[{index}].analysis",
                bindings,
                citations,
                used_aliases,
            )
            if item.overlap_level == OverlapLevel.HIGH and all(
                str(bindings[alias]["section_type"]) == "background"
                for alias in item.citation_aliases
            ):
                raise FollowupError("HIGH overlap cannot rely only on background text")
        for index, option in enumerate(answer.design_around_options):
            self._append_aliases(
                option.citation_aliases,
                f"design_around_options[{index}].remaining_risks",
                bindings,
                citations,
                used_aliases,
            )

        assertions = answer.model_dump(
            mode="json",
            exclude={"legal_boundary", "limitations"},
            exclude_none=True,
        )
        raw = json.dumps(
            assertions, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        lowered = raw.lower()
        forbidden = [value for value in _FORBIDDEN_LEGAL_CONCLUSIONS if value in lowered]
        if forbidden:
            raise FollowupError(
                "answer crosses the legal conclusion boundary: " + ", ".join(forbidden)
            )
        allowed_publications = {
            normalized
            for value in allowed_publication_numbers
            if (normalized := normalize_publication_number(value)) is not None
        }
        mentioned = {
            normalized
            for value in _PUBLICATION.findall(raw)
            if (normalized := normalize_publication_number(value)) is not None
        }
        unknown_publications = mentioned - allowed_publications
        if unknown_publications:
            raise FollowupError(
                "answer references unknown publications: "
                + ", ".join(sorted(unknown_publications))
            )
        return VerifiedFollowupAnswer(
            answer=answer,
            citations=tuple(citations),
            used_aliases=tuple(dict.fromkeys(used_aliases)),
        )

    @staticmethod
    def _append_aliases(
        aliases: list[str],
        answer_path: str,
        bindings: dict[str, Any],
        citations: list[FollowupCitationDraft],
        used_aliases: list[str],
    ) -> None:
        for alias in aliases:
            binding = bindings.get(alias)
            if binding is None:
                raise FollowupError(f"answer references unknown Citation alias: {alias}")
            excerpt = str(binding["excerpt"])
            citations.append(
                FollowupCitationDraft(
                    chunk_id=str(binding["chunk_id"]),
                    quote_text=excerpt,
                    start_offset=(
                        int(binding["start_offset"])
                        if binding.get("start_offset") is not None
                        else None
                    ),
                    end_offset=(
                        int(binding["start_offset"]) + len(excerpt)
                        if binding.get("start_offset") is not None
                        else None
                    ),
                    answer_path=answer_path,
                )
            )
            used_aliases.append(alias)


__all__ = [
    "DesignAroundOption",
    "FollowupAnswer",
    "FollowupAnswerType",
    "FollowupAnswerVerifier",
    "OverlapItem",
    "OverlapLevel",
    "VerifiedFollowupAnswer",
]
