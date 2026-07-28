from __future__ import annotations

from calendar import monthrange
from datetime import date
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class LandscapeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AnalysisMode(StrEnum):
    TECHNOLOGY = "TECHNOLOGY"
    COMPETITOR = "COMPETITOR"
    TECHNOLOGY_COMPETITOR = "TECHNOLOGY_COMPETITOR"


class PeriodPreset(StrEnum):
    ONE_MONTH = "ONE_MONTH"
    QUARTER = "QUARTER"
    SIX_MONTHS = "SIX_MONTHS"
    TWELVE_MONTHS = "TWELVE_MONTHS"
    CUSTOM = "CUSTOM"


_PRESET_MONTHS = {
    PeriodPreset.ONE_MONTH: 1,
    PeriodPreset.QUARTER: 3,
    PeriodPreset.SIX_MONTHS: 6,
    PeriodPreset.TWELVE_MONTHS: 12,
}


def subtract_calendar_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 - months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    day = min(value.day, monthrange(year, month)[1])
    return date(year, month, day)


class RunStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_LIMITATIONS = "COMPLETED_WITH_LIMITATIONS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class DocumentStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    SELECTED = "SELECTED"
    FETCHED = "FETCHED"
    ANALYZED = "ANALYZED"
    FAILED = "FAILED"
    EXCLUDED = "EXCLUDED"


class FamilyDataStatus(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"


class CompetitorInput(LandscapeModel):
    name: str = Field(min_length=1, max_length=200)
    aliases: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for raw in values:
            value = raw.strip()
            key = value.casefold()
            if not value or key in seen:
                continue
            seen.add(key)
            result.append(value)
        return result

    def confirmed_names(self) -> tuple[str, ...]:
        values = [self.name, *self.aliases]
        return tuple(dict.fromkeys(value.casefold() for value in values))


class AnalysisBudget(LandscapeModel):
    candidate_limit: int = Field(default=100, ge=10, le=200)
    analysis_limit: int = Field(default=20, ge=1, le=50)
    per_query_limit: int = Field(default=50, ge=5, le=100)

    @model_validator(mode="after")
    def coherent_limits(self) -> "AnalysisBudget":
        if self.analysis_limit > self.candidate_limit:
            raise ValueError("analysis_limit must be <= candidate_limit")
        return self


class LandscapeScope(LandscapeModel):
    mode: AnalysisMode | None = None
    technology_direction: str | None = Field(default=None, max_length=500)
    competitors: list[CompetitorInput] = Field(default_factory=list, max_length=20)
    period_preset: PeriodPreset = PeriodPreset.CUSTOM
    publication_start: date
    publication_end: date
    budget: AnalysisBudget = Field(default_factory=AnalysisBudget)

    @field_validator("technology_direction")
    @classmethod
    def blank_direction_is_none(cls, value: str | None) -> str | None:
        return value or None

    @model_validator(mode="after")
    def validate_scope(self) -> "LandscapeScope":
        preset_months = _PRESET_MONTHS.get(self.period_preset)
        if preset_months is not None:
            self.publication_start = subtract_calendar_months(
                self.publication_end, preset_months
            )
        if self.publication_end < self.publication_start:
            raise ValueError("publication_end must be >= publication_start")
        if (self.publication_end - self.publication_start).days > 366:
            raise ValueError("publication window cannot exceed 12 months")
        if not self.technology_direction and not self.competitors:
            raise ValueError("technology_direction or competitors is required")
        derived = (
            AnalysisMode.TECHNOLOGY_COMPETITOR
            if self.technology_direction and self.competitors
            else AnalysisMode.TECHNOLOGY
            if self.technology_direction
            else AnalysisMode.COMPETITOR
        )
        if self.mode is not None and self.mode != derived:
            raise ValueError(f"mode does not match supplied inputs; expected {derived.value}")
        self.mode = derived
        competitor_keys = [competitor.name.casefold() for competitor in self.competitors]
        if len(competitor_keys) != len(set(competitor_keys)):
            raise ValueError("competitor names must be unique")
        return self


class CompetitorAliasResolution(LandscapeModel):
    primary_name: str = Field(min_length=1, max_length=200)
    aliases: list[str] = Field(default_factory=list, max_length=12)
    source: Literal["MODEL_INFERRED", "PRIMARY_NAME_FALLBACK"]

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for raw in values:
            value = raw.strip()
            key = value.casefold()
            if value and key not in seen:
                seen.add(key)
                result.append(value)
        return result


class CompetitorAliasPlan(LandscapeModel):
    competitors: list[CompetitorAliasResolution] = Field(min_length=1, max_length=20)


class TechnicalDirectionExpansion(LandscapeModel):
    original_term: str = Field(min_length=1, max_length=500)
    chinese_terms: list[str] = Field(min_length=1, max_length=8)
    english_terms: list[str] = Field(min_length=1, max_length=8)
    source: Literal["MODEL_INFERRED"]

    @field_validator("chinese_terms", "english_terms")
    @classmethod
    def normalize_terms(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for raw in values:
            value = " ".join(raw.split())
            key = value.casefold()
            if value and key not in seen:
                seen.add(key)
                result.append(value)
        return result


class LandscapePlannedQuery(LandscapeModel):
    query_text: str = Field(min_length=2, max_length=500)
    language: Literal["zh", "en", "mixed"]
    rationale: str = Field(min_length=1, max_length=500)


class LandscapeQueryPlan(LandscapeModel):
    direction_terms: list[str] = Field(default_factory=list, max_length=30)
    direction_english_terms: list[str] = Field(default_factory=list, max_length=8)
    queries: list[LandscapePlannedQuery] = Field(min_length=1, max_length=40)


class EvidenceItem(LandscapeModel):
    evidence_id: str = Field(pattern=r"^EV-[A-Za-z0-9._-]+$")
    publication_number: str = Field(min_length=2, max_length=100)
    section_type: Literal["ABSTRACT", "CLAIM", "BACKGROUND", "DESCRIPTION"]
    section_label: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1)
    start_offset: int = Field(ge=0)
    end_offset: int = Field(gt=0)
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def offsets_are_ordered(self) -> "EvidenceItem":
        if self.end_offset <= self.start_offset:
            raise ValueError("end_offset must be greater than start_offset")
        return self


class LandscapeEvidenceRef(LandscapeModel):
    evidence_id: str = Field(pattern=r"^EV-[A-Za-z0-9._-]+$")
    supports: list[
        Literal[
            "prior_art",
            "prior_art_problem",
            "core_invention_point",
            "technical_problem_solved",
            "beneficial_effect",
        ]
    ] = Field(min_length=1)


class LandscapePatentAnalysis(LandscapeModel):
    publication_number: str = Field(min_length=2, max_length=100)
    prior_art: str = Field(min_length=1)
    prior_art_problems: list[str] = Field(default_factory=list, max_length=20)
    core_invention_points: list[str] = Field(min_length=1, max_length=20)
    technical_problems_solved: list[str] = Field(default_factory=list, max_length=20)
    beneficial_effects: list[str] = Field(default_factory=list, max_length=20)
    technical_keywords: list[str] = Field(default_factory=list, max_length=30)
    evidence_refs: list[LandscapeEvidenceRef] = Field(min_length=1, max_length=100)
    limitations: list[str] = Field(default_factory=list, max_length=20)


class LandscapeCluster(LandscapeModel):
    cluster_id: str = Field(pattern=r"^CL-[A-Za-z0-9._-]+$")
    name: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=2000)
    keywords: list[str] = Field(default_factory=list, max_length=30)
    publication_numbers: list[str] = Field(min_length=1)


class LandscapeClusterPlan(LandscapeModel):
    clusters: list[LandscapeCluster] = Field(min_length=1, max_length=8)


CompanyId = Annotated[
    str,
    Field(pattern=r"^(?:CO-[A-Za-z0-9._-]+|UNKNOWN)$"),
]
PublicationNumber = Annotated[str, Field(min_length=2, max_length=100)]
EvidenceId = Annotated[str, Field(pattern=r"^EV-[A-Za-z0-9._-]+$")]


def _normalized_unique_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = " ".join(raw.split())
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            result.append(value)
    return result


class NormalizedCompany(LandscapeModel):
    """One deterministic company identity; model suggestions are validated elsewhere."""

    company_id: CompanyId
    canonical_name: str = Field(min_length=1, max_length=200)
    aliases: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, values: list[str]) -> list[str]:
        return _normalized_unique_strings(values)

    @model_validator(mode="after")
    def unknown_identity_is_explicit(self) -> "NormalizedCompany":
        if self.company_id == "UNKNOWN":
            if self.canonical_name != "UNKNOWN" or self.aliases:
                raise ValueError(
                    "UNKNOWN company must use canonical_name UNKNOWN and have no aliases"
                )
        elif self.canonical_name == "UNKNOWN":
            raise ValueError("only the UNKNOWN company may use canonical_name UNKNOWN")
        return self


class CompanyAssignment(LandscapeModel):
    """Program-owned primary assignment for exactly one eligible publication."""

    publication_number: PublicationNumber
    primary_company_id: CompanyId
    observed_assignee: str | None = Field(default=None, max_length=200)
    matched_alias: str | None = Field(default=None, max_length=200)
    co_assignees: list[str] = Field(default_factory=list, max_length=20)
    status: Literal[
        "CONFIRMED_ALIAS",
        "NORMALIZED_NAME",
        "UNKNOWN",
        "REVIEW_REQUIRED",
    ]

    @field_validator("observed_assignee", "matched_alias")
    @classmethod
    def blank_optional_text_is_none(cls, value: str | None) -> str | None:
        return value or None

    @field_validator("co_assignees")
    @classmethod
    def normalize_co_assignees(cls, values: list[str]) -> list[str]:
        return _normalized_unique_strings(values)

    @model_validator(mode="after")
    def status_matches_assignment(self) -> "CompanyAssignment":
        if self.status == "CONFIRMED_ALIAS":
            if self.primary_company_id == "UNKNOWN":
                raise ValueError("confirmed alias assignment cannot target UNKNOWN")
            if self.observed_assignee is None or self.matched_alias is None:
                raise ValueError(
                    "confirmed alias assignment requires observed_assignee and matched_alias"
                )
        elif self.status == "NORMALIZED_NAME":
            if self.primary_company_id == "UNKNOWN":
                raise ValueError("normalized-name assignment cannot target UNKNOWN")
            if self.observed_assignee is None:
                raise ValueError(
                    "normalized-name assignment requires observed_assignee"
                )
            if self.matched_alias is not None:
                raise ValueError(
                    "normalized-name assignment cannot include matched_alias"
                )
        else:
            if self.primary_company_id != "UNKNOWN":
                raise ValueError(
                    "unknown or review-required assignment must target UNKNOWN"
                )
            if self.status == "UNKNOWN" and self.matched_alias is not None:
                raise ValueError("unknown assignment cannot include matched_alias")
        return self


class CompanyTechnologyCategory(LandscapeModel):
    category_id: str = Field(pattern=r"^TC-[A-Za-z0-9._-]+$")
    name: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=2000)
    keywords: list[str] = Field(default_factory=list, max_length=30)
    publication_numbers: list[PublicationNumber] = Field(min_length=1)
    evidence_ids: list[EvidenceId] = Field(min_length=1, max_length=200)

    @field_validator("keywords")
    @classmethod
    def normalize_keywords(cls, values: list[str]) -> list[str]:
        return _normalized_unique_strings(values)

    @model_validator(mode="after")
    def memberships_are_unique(self) -> "CompanyTechnologyCategory":
        if len(self.publication_numbers) != len(set(self.publication_numbers)):
            raise ValueError("category publication numbers must be unique")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("category evidence IDs must be unique")
        return self


class CompanyTechnologyClassification(LandscapeModel):
    """One company's model-proposed categories before program-owned profiling."""

    technology_categories: list[CompanyTechnologyCategory] = Field(
        min_length=1, max_length=20
    )

    @model_validator(mode="after")
    def category_ids_and_memberships_are_unique(
        self,
    ) -> "CompanyTechnologyClassification":
        category_ids = [
            category.category_id for category in self.technology_categories
        ]
        if len(category_ids) != len(set(category_ids)):
            raise ValueError("company technology category IDs must be unique")
        publications = [
            publication
            for category in self.technology_categories
            for publication in category.publication_numbers
        ]
        if len(publications) != len(set(publications)):
            raise ValueError(
                "a publication may appear in only one company technology category"
            )
        return self


class CompanyTechnologyProfile(LandscapeModel):
    """LLM output only; company ID and expected publications remain program-owned."""

    overall_summary: str = Field(min_length=1, max_length=4000)
    technology_directions: list[str] = Field(min_length=1, max_length=20)
    technology_categories: list[CompanyTechnologyCategory] = Field(
        min_length=1, max_length=20
    )
    limitations: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("technology_directions", "limitations")
    @classmethod
    def normalize_text_lists(cls, values: list[str]) -> list[str]:
        return _normalized_unique_strings(values)

    @model_validator(mode="after")
    def categories_form_one_partition(self) -> "CompanyTechnologyProfile":
        category_ids = [
            category.category_id for category in self.technology_categories
        ]
        if len(category_ids) != len(set(category_ids)):
            raise ValueError("company technology category IDs must be unique")
        publications = [
            publication
            for category in self.technology_categories
            for publication in category.publication_numbers
        ]
        if len(publications) != len(set(publications)):
            raise ValueError(
                "a publication may appear in only one company technology category"
            )
        return self


class TrendTimeBasis(LandscapeModel):
    start: date
    end: date
    bucket: Literal["MONTH", "QUARTER"]

    @model_validator(mode="after")
    def dates_are_ordered(self) -> "TrendTimeBasis":
        if self.end < self.start:
            raise ValueError("trend time basis end must be >= start")
        return self


class CrossCompanyTrend(LandscapeModel):
    trend_id: str = Field(pattern=r"^TR-[A-Za-z0-9._-]+$")
    name: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=3000)
    direction: Literal[
        "EMERGING",
        "GROWING",
        "DECLINING",
        "SHIFTING",
        "ACCELERATING",
        "STABLE",
        "UNCERTAIN",
    ]
    company_ids: list[CompanyId] = Field(min_length=2, max_length=50)
    publication_numbers: list[PublicationNumber] = Field(min_length=2)
    evidence_ids: list[EvidenceId] = Field(min_length=1, max_length=500)
    time_basis: TrendTimeBasis

    @model_validator(mode="after")
    def references_are_unique(self) -> "CrossCompanyTrend":
        for label, values in (
            ("company IDs", self.company_ids),
            ("publication numbers", self.publication_numbers),
            ("evidence IDs", self.evidence_ids),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"trend {label} must be unique")
        return self


class CrossCompanyTrendAnalysis(LandscapeModel):
    """LLM output only; time buckets and company count remain program-owned."""

    overall_summary: str = Field(min_length=1, max_length=4000)
    common_directions: list[str] = Field(default_factory=list, max_length=20)
    differentiated_directions: list[str] = Field(default_factory=list, max_length=20)
    trends: list[CrossCompanyTrend] = Field(default_factory=list, max_length=20)
    limitations: list[str] = Field(default_factory=list, max_length=20)

    @field_validator(
        "common_directions",
        "differentiated_directions",
        "limitations",
    )
    @classmethod
    def normalize_text_lists(cls, values: list[str]) -> list[str]:
        return _normalized_unique_strings(values)

    @model_validator(mode="after")
    def trend_ids_are_unique(self) -> "CrossCompanyTrendAnalysis":
        trend_ids = [trend.trend_id for trend in self.trends]
        if len(trend_ids) != len(set(trend_ids)):
            raise ValueError("cross-company trend IDs must be unique")
        return self


class LandscapeCoverageAudit(LandscapeModel):
    decision: Literal["PASS", "REPAIR", "LIMITED", "FAIL"]
    coverage_ratio: float = Field(ge=0, le=1)
    invented_publications: list[PublicationNumber] = Field(
        default_factory=list, max_length=500
    )
    duplicate_memberships: list[PublicationNumber] = Field(
        default_factory=list, max_length=500
    )
    missing_publications: list[PublicationNumber] = Field(
        default_factory=list, max_length=500
    )
    invalid_evidence_refs: list[EvidenceId] = Field(
        default_factory=list, max_length=500
    )
    repair_targets: list[str] = Field(default_factory=list, max_length=500)
    limitations: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("repair_targets", "limitations")
    @classmethod
    def normalize_text_lists(cls, values: list[str]) -> list[str]:
        return _normalized_unique_strings(values)

    @model_validator(mode="after")
    def decision_matches_findings(self) -> "LandscapeCoverageAudit":
        findings = (
            self.invented_publications,
            self.duplicate_memberships,
            self.missing_publications,
            self.invalid_evidence_refs,
        )
        if self.decision == "PASS":
            if self.coverage_ratio != 1 or any(findings) or self.repair_targets:
                raise ValueError(
                    "PASS requires full coverage and no audit findings or repair targets"
                )
        elif self.decision == "REPAIR" and not self.repair_targets:
            raise ValueError("REPAIR requires at least one repair target")
        return self
