from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import date
from enum import StrEnum
from typing import Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_STABLE_ID = re.compile(r"^[A-Z]+-[0-9a-f]{16}$")


class ScopeValidationError(ValueError):
    pass


class ScopeModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class LandscapeInputMode(StrEnum):
    COMPANY_ONLY = "COMPANY_ONLY"
    TECHNOLOGY_ONLY = "TECHNOLOGY_ONLY"
    COMPANY_AND_TECHNOLOGY = "COMPANY_AND_TECHNOLOGY"


class ScopeDraftStatus(StrEnum):
    DRAFT = "DRAFT"
    EXPANDING = "EXPANDING"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    CONFIRMED = "CONFIRMED"


class CandidateStatus(StrEnum):
    PROPOSED = "PROPOSED"
    ACTIVE = "ACTIVE"
    EXCLUDED = "EXCLUDED"


class CandidateSource(StrEnum):
    USER_INPUT = "USER_INPUT"
    USER_ADDED = "USER_ADDED"
    HISTORY = "HISTORY"
    MODEL_SUGGESTED = "MODEL_SUGGESTED"


class CompanyMemoryAction(StrEnum):
    """How an excluded company name affects future company-profile recall."""

    NONE = "NONE"
    REJECT = "REJECT"
    RETIRE = "RETIRE"


class NameLanguage(StrEnum):
    ZH = "ZH"
    EN = "EN"
    OTHER = "OTHER"


class CompanyNameRelation(StrEnum):
    LEGAL_NAME = "LEGAL_NAME"
    TRANSLATION = "TRANSLATION"
    ALIAS = "ALIAS"
    FORMER_NAME = "FORMER_NAME"
    SUBSIDIARY = "SUBSIDIARY"
    GROUP_MEMBER = "GROUP_MEMBER"


class TechnologyTermRelation(StrEnum):
    ORIGINAL = "ORIGINAL"
    TRANSLATION = "TRANSLATION"
    SYNONYM = "SYNONYM"
    ABBREVIATION = "ABBREVIATION"
    BROADER = "BROADER"
    NARROWER = "NARROWER"
    COMPONENT = "COMPONENT"
    RELATED = "RELATED"


class ScopeDraftLimitation(ScopeModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")
    object_key: str = Field(min_length=1, max_length=300)
    message: str = Field(min_length=1, max_length=1000)


class CompanyNameCandidate(ScopeModel):
    name_id: str
    text: str = Field(min_length=1, max_length=300)
    normalized_text: str = Field(min_length=1, max_length=300)
    language: NameLanguage
    relation_type: CompanyNameRelation
    source: CandidateSource
    status: CandidateStatus = CandidateStatus.PROPOSED
    memory_action: CompanyMemoryAction = CompanyMemoryAction.NONE
    rationale: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_program_identity(self) -> "CompanyNameCandidate":
        if not _STABLE_ID.fullmatch(self.name_id) or not self.name_id.startswith("CNM-"):
            raise ValueError("company name ID must be a stable CNM identifier")
        if self.normalized_text != normalize_scope_text(self.text):
            raise ValueError("company normalized text does not match text")
        if self.status != CandidateStatus.EXCLUDED and self.memory_action != CompanyMemoryAction.NONE:
            raise ValueError("only an excluded company name can change long-term memory")
        return self


class CompanyScopeDraft(ScopeModel):
    profile_id: str
    display_name: str = Field(min_length=1, max_length=300)
    input_name: str = Field(min_length=1, max_length=300)
    names: tuple[CompanyNameCandidate, ...] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def unique_company_names(self) -> "CompanyScopeDraft":
        if not _STABLE_ID.fullmatch(self.profile_id) or not self.profile_id.startswith("CMP-"):
            raise ValueError("company profile ID must be a stable CMP identifier")
        identifiers = [item.name_id for item in self.names]
        normalized = [item.normalized_text for item in self.names]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("company name IDs must be unique")
        if len(normalized) != len(set(normalized)):
            raise ValueError("company candidate names must be unique after normalization")
        return self


class TechnologyTermCandidate(ScopeModel):
    term_id: str
    text: str = Field(min_length=1, max_length=300)
    normalized_text: str = Field(min_length=1, max_length=300)
    language: NameLanguage
    relation_to_original: TechnologyTermRelation
    source: CandidateSource
    status: CandidateStatus = CandidateStatus.PROPOSED
    rationale: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_program_identity(self) -> "TechnologyTermCandidate":
        if not _STABLE_ID.fullmatch(self.term_id) or not self.term_id.startswith("TRM-"):
            raise ValueError("technology term ID must be a stable TRM identifier")
        if self.normalized_text != normalize_scope_text(self.text):
            raise ValueError("technology term normalized text does not match text")
        if self.language == NameLanguage.OTHER:
            raise ValueError("technology terms must be marked ZH or EN")
        return self


class ScopeDraft(ScopeModel):
    draft_id: str
    revision: int = Field(default=1, ge=1)
    status: ScopeDraftStatus = ScopeDraftStatus.DRAFT
    publication_start: date
    publication_end: date
    companies: tuple[CompanyScopeDraft, ...] = Field(default=(), max_length=50)
    technology_input: str | None = Field(default=None, max_length=500)
    technology_terms: tuple[TechnologyTermCandidate, ...] = Field(
        default=(), max_length=300
    )
    limitations: tuple[ScopeDraftLimitation, ...] = Field(default=(), max_length=100)

    @field_validator("technology_input")
    @classmethod
    def blank_technology_is_none(cls, value: str | None) -> str | None:
        return value or None

    @model_validator(mode="after")
    def validate_draft_boundary(self) -> "ScopeDraft":
        if not _STABLE_ID.fullmatch(self.draft_id) or not self.draft_id.startswith("SCD-"):
            raise ValueError("scope draft ID must be a stable SCD identifier")
        if self.publication_end < self.publication_start:
            raise ValueError("publication_end must be on or after publication_start")
        if not self.companies and not self.technology_input:
            raise ValueError("at least one company or a technology direction is required")
        if not self.technology_input and self.technology_terms:
            raise ValueError("technology terms require a technology input")
        profile_ids = [company.profile_id for company in self.companies]
        if len(profile_ids) != len(set(profile_ids)):
            raise ValueError("company profile IDs must be unique in a scope draft")
        term_ids = [term.term_id for term in self.technology_terms]
        term_values = [term.normalized_text for term in self.technology_terms]
        if len(term_ids) != len(set(term_ids)):
            raise ValueError("technology term IDs must be unique")
        if len(term_values) != len(set(term_values)):
            raise ValueError("technology terms must be unique after normalization")
        return self

    @property
    def mode(self) -> LandscapeInputMode:
        if self.companies and self.technology_input:
            return LandscapeInputMode.COMPANY_AND_TECHNOLOGY
        if self.companies:
            return LandscapeInputMode.COMPANY_ONLY
        return LandscapeInputMode.TECHNOLOGY_ONLY


class ConfirmedCompanyName(ScopeModel):
    name_id: str
    text: str
    normalized_text: str
    language: NameLanguage
    relation_type: CompanyNameRelation


class ConfirmedCompanyScope(ScopeModel):
    profile_id: str
    profile_version: int = Field(ge=1)
    display_name: str
    names: tuple[ConfirmedCompanyName, ...] = Field(min_length=1)


class ConfirmedTechnologyTerm(ScopeModel):
    term_id: str
    text: str
    normalized_text: str
    language: NameLanguage
    relation_to_original: TechnologyTermRelation


class ConfirmedScopeRevision(ScopeModel):
    scope_revision_id: str
    scope_revision_hash: str
    source_draft_id: str
    source_draft_revision: int
    mode: LandscapeInputMode
    publication_start: date
    publication_end: date
    companies: tuple[ConfirmedCompanyScope, ...]
    technology_input: str | None
    technology_terms: tuple[ConfirmedTechnologyTerm, ...]


def freeze_scope_draft(
    draft: ScopeDraft,
    company_profile_versions: Mapping[str, int] | None = None,
) -> ConfirmedScopeRevision:
    if draft.status != ScopeDraftStatus.AWAITING_CONFIRMATION:
        raise ScopeValidationError(
            "scope draft must be AWAITING_CONFIRMATION before it can be frozen"
        )

    unresolved_candidates = [
        item.name_id
        for company in draft.companies
        for item in company.names
        if item.status == CandidateStatus.PROPOSED
    ] + [
        item.term_id
        for item in draft.technology_terms
        if item.status == CandidateStatus.PROPOSED
    ]
    if unresolved_candidates:
        raise ScopeValidationError(
            "all proposed company names and technology terms must be reviewed"
        )

    profile_versions = dict(company_profile_versions or {})
    expected_profile_ids = {company.profile_id for company in draft.companies}
    if set(profile_versions) != expected_profile_ids:
        raise ScopeValidationError(
            "company profile versions must match the draft company set exactly"
        )
    if any(
        not isinstance(version, int) or isinstance(version, bool) or version < 1
        for version in profile_versions.values()
    ):
        raise ScopeValidationError("company profile versions must be positive integers")

    companies: list[ConfirmedCompanyScope] = []
    active_name_owner: dict[str, str] = {}
    for company in draft.companies:
        active_names = [
            item for item in company.names if item.status == CandidateStatus.ACTIVE
        ]
        if not active_names:
            raise ScopeValidationError(
                f"company {company.display_name!r} must keep at least one active name"
            )
        for item in active_names:
            owner = active_name_owner.setdefault(item.normalized_text, company.profile_id)
            if owner != company.profile_id:
                raise ScopeValidationError(
                    f"active company name belongs to multiple companies: {item.text}"
                )
        companies.append(
            ConfirmedCompanyScope(
                profile_id=company.profile_id,
                profile_version=profile_versions[company.profile_id],
                display_name=company.display_name,
                names=tuple(
                    ConfirmedCompanyName(
                        name_id=item.name_id,
                        text=item.text,
                        normalized_text=item.normalized_text,
                        language=item.language,
                        relation_type=item.relation_type,
                    )
                    for item in active_names
                ),
            )
        )

    active_terms = [
        item for item in draft.technology_terms if item.status == CandidateStatus.ACTIVE
    ]
    if draft.technology_input:
        languages = {item.language for item in active_terms}
        if NameLanguage.ZH not in languages or NameLanguage.EN not in languages:
            raise ScopeValidationError(
                "technology scope must contain at least one active ZH and EN term"
            )

    semantic = {
        "source_draft_id": draft.draft_id,
        "source_draft_revision": draft.revision,
        "mode": draft.mode.value,
        "publication_start": draft.publication_start.isoformat(),
        "publication_end": draft.publication_end.isoformat(),
        "companies": [company.model_dump(mode="json") for company in companies],
        "technology_input": draft.technology_input,
        "technology_terms": [
            ConfirmedTechnologyTerm(
                term_id=item.term_id,
                text=item.text,
                normalized_text=item.normalized_text,
                language=item.language,
                relation_to_original=item.relation_to_original,
            ).model_dump(mode="json")
            for item in active_terms
        ],
    }
    content_hash = _hash_json(semantic)
    return ConfirmedScopeRevision(
        scope_revision_id=f"SCR-{content_hash[:16]}",
        scope_revision_hash=content_hash,
        source_draft_id=draft.draft_id,
        source_draft_revision=draft.revision,
        mode=draft.mode,
        publication_start=draft.publication_start,
        publication_end=draft.publication_end,
        companies=tuple(companies),
        technology_input=draft.technology_input,
        technology_terms=tuple(
            ConfirmedTechnologyTerm.model_validate(item)
            for item in semantic["technology_terms"]
        ),
    )


def make_company_profile_id(anchor_name: str) -> str:
    return f"CMP-{_hash_text(normalize_scope_text(anchor_name))[:16]}"


def make_company_name_candidate(
    *,
    profile_id: str,
    text: str,
    language: NameLanguage,
    relation_type: CompanyNameRelation,
    source: CandidateSource,
    status: CandidateStatus = CandidateStatus.PROPOSED,
    memory_action: CompanyMemoryAction = CompanyMemoryAction.NONE,
    rationale: str | None = None,
) -> CompanyNameCandidate:
    normalized = normalize_scope_text(text)
    identity = _hash_text(f"{profile_id}\x1f{normalized}\x1f{relation_type.value}")
    return CompanyNameCandidate(
        name_id=f"CNM-{identity[:16]}",
        text=text,
        normalized_text=normalized,
        language=language,
        relation_type=relation_type,
        source=source,
        status=status,
        memory_action=memory_action,
        rationale=rationale,
    )


def make_technology_term_candidate(
    *,
    text: str,
    language: NameLanguage,
    relation_to_original: TechnologyTermRelation,
    source: CandidateSource,
    status: CandidateStatus = CandidateStatus.PROPOSED,
    rationale: str | None = None,
) -> TechnologyTermCandidate:
    normalized = normalize_scope_text(text)
    identity = _hash_text(f"{normalized}\x1f{language.value}\x1f{relation_to_original.value}")
    return TechnologyTermCandidate(
        term_id=f"TRM-{identity[:16]}",
        text=text,
        normalized_text=normalized,
        language=language,
        relation_to_original=relation_to_original,
        source=source,
        status=status,
        rationale=rationale,
    )


def make_scope_draft_id(seed: str) -> str:
    return f"SCD-{_hash_text(seed)[:16]}"


def normalize_scope_text(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("scope text must be a string")
    normalized = " ".join(unicodedata.normalize("NFKC", value).split()).casefold()
    if not normalized:
        raise ValueError("scope text must not be blank")
    return normalized


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash_json(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _hash_text(encoded)


__all__ = [
    "CandidateSource",
    "CandidateStatus",
    "CompanyNameCandidate",
    "CompanyNameRelation",
    "CompanyMemoryAction",
    "CompanyScopeDraft",
    "ConfirmedScopeRevision",
    "LandscapeInputMode",
    "NameLanguage",
    "ScopeDraft",
    "ScopeDraftStatus",
    "ScopeDraftLimitation",
    "ScopeValidationError",
    "TechnologyTermCandidate",
    "TechnologyTermRelation",
    "freeze_scope_draft",
    "make_company_name_candidate",
    "make_company_profile_id",
    "make_scope_draft_id",
    "make_technology_term_candidate",
    "normalize_scope_text",
]
