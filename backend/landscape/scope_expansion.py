from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from idea.agent_schemas import register_agent_output_model
from idea.model_client import StructuredModelClient

from .scope import (
    CandidateSource,
    CandidateStatus,
    CompanyNameRelation,
    CompanyScopeDraft,
    NameLanguage,
    TechnologyTermCandidate,
    TechnologyTermRelation,
    make_company_name_candidate,
    make_company_profile_id,
    make_technology_term_candidate,
)
from .scope_repository import CompanyProfileMemory


COMPANY_SCOPE_EXPANDER = "patent-landscape-v4-company-scope-expander"
TECHNOLOGY_SCOPE_EXPANDER = "patent-landscape-v4-technology-scope-expander"

COMPANY_PROMPT = """
You prepare a reviewable patent-assignee search scope for exactly one user-named company or group.
Return bilingual Chinese/English legal names, translations, common abbreviations, former names, and
only clearly related subsidiaries or group members. Distinguish ALIAS from SUBSIDIARY and
GROUP_MEMBER. Never silently merge an unrelated entity, product, or brand. Copy input_name exactly.
Existing ACTIVE and EXCLUDED names are evidence: propose only genuinely new incremental candidates,
and never repeat an EXCLUDED name. Every candidate needs a concise Simplified-Chinese rationale.
Return no more than 24 candidates total. Prioritize legal names, translations, abbreviations, and
former names; include no more than 8 high-confidence subsidiaries/group members and never attempt
an exhaustive corporate registry listing.
The user will review every proposal; do not decide ACTIVE/EXCLUDED status.
"""

TECHNOLOGY_PROMPT = """
You prepare a reviewable bilingual patent search vocabulary for exactly one technology direction.
Judge semantic breadth from meaning, not string length. For a broad direction, add only necessary
translations, established names, and bounded synonyms. For a narrow direction, add translations,
synonyms, abbreviations, broader/narrower terms, key components, and closely related patent wording
that improves recall without crossing into a different technology. Copy original_input exactly.
Return at least one useful Chinese and one useful English candidate. Every candidate needs a concise
Simplified-Chinese rationale. The user will review all proposals; do not activate them yourself.
Return 12-36 candidates total; prefer precision over filling the maximum.
"""


class ExpansionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class ProposedCompanyName(ExpansionModel):
    text: str = Field(min_length=1, max_length=300)
    language: NameLanguage
    relation_type: CompanyNameRelation
    rationale: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def language_and_rationale_match_content(self) -> "ProposedCompanyName":
        _validate_declared_language(self.text, self.language)
        _require_chinese_rationale(self.rationale)
        return self


class CompanyExpansionOutput(ExpansionModel):
    input_name: str = Field(min_length=1, max_length=300)
    # The wire schema tolerates an over-verbose provider response so a harmless
    # size excess does not trigger several expensive model retries. The service
    # below applies the stricter deterministic product budget.
    candidates: tuple[ProposedCompanyName, ...] = Field(max_length=160)


class ProposedTechnologyTerm(ExpansionModel):
    text: str = Field(min_length=1, max_length=300)
    language: NameLanguage
    relation_to_original: TechnologyTermRelation
    rationale: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def technology_language_is_bilingual(self) -> "ProposedTechnologyTerm":
        if self.language == NameLanguage.OTHER:
            raise ValueError("technology term language must be ZH or EN")
        _validate_declared_language(self.text, self.language)
        _require_chinese_rationale(self.rationale)
        return self


class TechnologyExpansionOutput(ExpansionModel):
    original_input: str = Field(min_length=1, max_length=500)
    semantic_breadth: Literal["BROAD", "NARROW", "BALANCED", "UNCERTAIN"]
    candidates: tuple[ProposedTechnologyTerm, ...] = Field(min_length=2, max_length=120)

    @model_validator(mode="after")
    def candidates_are_bilingual(self) -> "TechnologyExpansionOutput":
        languages = {item.language for item in self.candidates}
        if NameLanguage.ZH not in languages or NameLanguage.EN not in languages:
            raise ValueError("technology expansion must contain ZH and EN candidates")
        return self


class ScopeExpansionError(RuntimeError):
    pass


class ScopeExpansionService:
    def __init__(self, model: StructuredModelClient):
        register_agent_output_model(COMPANY_SCOPE_EXPANDER, CompanyExpansionOutput)
        register_agent_output_model(
            TECHNOLOGY_SCOPE_EXPANDER, TechnologyExpansionOutput
        )
        self.model = model

    async def expand_company(
        self,
        input_name: str,
        *,
        memory: CompanyProfileMemory | None = None,
    ) -> CompanyScopeDraft:
        base = memory.company if memory is not None else _new_company(input_name)
        result = await self.model.complete(
            COMPANY_SCOPE_EXPANDER,
            system_prompt=COMPANY_PROMPT,
            input_payload={
                "input_name": input_name,
                "existing_names": [item.model_dump(mode="json") for item in base.names],
            },
        )
        output = result.output
        if not isinstance(output, CompanyExpansionOutput):
            raise ScopeExpansionError("company scope expander returned the wrong schema")
        if output.input_name != input_name:
            raise ScopeExpansionError("company scope expander changed the input company")

        names = list(base.names)
        seen = {item.normalized_text for item in names}
        for proposal in output.candidates:
            candidate = make_company_name_candidate(
                profile_id=base.profile_id,
                text=proposal.text,
                language=proposal.language,
                relation_type=proposal.relation_type,
                source=CandidateSource.MODEL_SUGGESTED,
                status=CandidateStatus.PROPOSED,
                rationale=proposal.rationale,
            )
            if candidate.normalized_text in seen:
                continue
            seen.add(candidate.normalized_text)
            names.append(candidate)
            if len(names) - len(base.names) == 40:
                break
        return CompanyScopeDraft(
            profile_id=base.profile_id,
            display_name=base.display_name,
            input_name=input_name,
            names=tuple(names),
        )

    async def expand_technology(
        self,
        original_input: str,
    ) -> tuple[TechnologyTermCandidate, ...]:
        result = await self.model.complete(
            TECHNOLOGY_SCOPE_EXPANDER,
            system_prompt=TECHNOLOGY_PROMPT,
            input_payload={"original_input": original_input},
        )
        output = result.output
        if not isinstance(output, TechnologyExpansionOutput):
            raise ScopeExpansionError("technology scope expander returned the wrong schema")
        if output.original_input != original_input:
            raise ScopeExpansionError("technology scope expander changed the original input")

        original = make_technology_term_candidate(
            text=original_input,
            language=_detect_language(original_input),
            relation_to_original=TechnologyTermRelation.ORIGINAL,
            source=CandidateSource.USER_INPUT,
            status=CandidateStatus.ACTIVE,
        )
        terms = [original]
        seen = {original.normalized_text}
        for proposal in output.candidates:
            candidate = make_technology_term_candidate(
                text=proposal.text,
                language=proposal.language,
                relation_to_original=proposal.relation_to_original,
                source=CandidateSource.MODEL_SUGGESTED,
                status=CandidateStatus.PROPOSED,
                rationale=proposal.rationale,
            )
            if candidate.normalized_text in seen:
                continue
            seen.add(candidate.normalized_text)
            terms.append(candidate)
            if len(terms) == 61:
                break
        return tuple(terms)


def fallback_company_scope(
    input_name: str,
    *,
    memory: CompanyProfileMemory | None = None,
) -> CompanyScopeDraft:
    base = memory.company if memory is not None else _new_company(input_name)
    return base.model_copy(update={"input_name": input_name})


def fallback_technology_terms(original_input: str) -> tuple[TechnologyTermCandidate, ...]:
    return (
        make_technology_term_candidate(
            text=original_input,
            language=_detect_language(original_input),
            relation_to_original=TechnologyTermRelation.ORIGINAL,
            source=CandidateSource.USER_INPUT,
            status=CandidateStatus.ACTIVE,
        ),
    )


def _new_company(input_name: str) -> CompanyScopeDraft:
    profile_id = make_company_profile_id(input_name)
    return CompanyScopeDraft(
        profile_id=profile_id,
        display_name=input_name,
        input_name=input_name,
        names=(
            make_company_name_candidate(
                profile_id=profile_id,
                text=input_name,
                language=_detect_language(input_name),
                relation_type=CompanyNameRelation.ALIAS,
                source=CandidateSource.USER_INPUT,
                status=CandidateStatus.ACTIVE,
            ),
        ),
    )


def _detect_language(value: str) -> NameLanguage:
    if re.search(r"[\u3400-\u4dbf\u4e00-\u9fff]", value):
        return NameLanguage.ZH
    if re.search(r"[A-Za-z]", value):
        return NameLanguage.EN
    return NameLanguage.OTHER


def _validate_declared_language(value: str, language: NameLanguage) -> None:
    if language == NameLanguage.ZH and not re.search(
        r"[\u3400-\u4dbf\u4e00-\u9fff]", value
    ):
        raise ValueError("ZH candidate must contain Chinese text")
    if language == NameLanguage.EN and not re.search(r"[A-Za-z]", value):
        raise ValueError("EN candidate must contain Latin text")


def _require_chinese_rationale(value: str) -> None:
    if not re.search(r"[\u3400-\u4dbf\u4e00-\u9fff]", value):
        raise ValueError("candidate rationale must use Simplified Chinese")


__all__ = [
    "COMPANY_SCOPE_EXPANDER",
    "TECHNOLOGY_SCOPE_EXPANDER",
    "CompanyExpansionOutput",
    "ProposedCompanyName",
    "ProposedTechnologyTerm",
    "ScopeExpansionError",
    "ScopeExpansionService",
    "TechnologyExpansionOutput",
    "fallback_company_scope",
    "fallback_technology_terms",
]
