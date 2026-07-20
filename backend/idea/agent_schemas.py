from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AgentModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceSpan(AgentModel):
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    text: str

    @model_validator(mode="after")
    def valid_span(self) -> "SourceSpan":
        if self.end < self.start:
            raise ValueError("source span end must be >= start")
        return self


class IdeaFeature(AgentModel):
    feature_id: str = Field(pattern=r"^F[1-9][0-9]*$")
    feature_text: str = Field(min_length=2)
    source_type: Literal["explicit", "normalized", "inferred"]
    source_span: SourceSpan | None = None
    required: bool = True

    @model_validator(mode="after")
    def explicit_feature_has_span(self) -> "IdeaFeature":
        if self.source_type == "explicit" and self.source_span is None:
            raise ValueError("explicit feature must include source_span")
        return self


class IdeaParserOutput(AgentModel):
    title: str = Field(min_length=2)
    technical_domains: list[str] = Field(min_length=1)
    application_scenario: str
    technical_problem: str = Field(min_length=2)
    features: list[IdeaFeature] = Field(min_length=1)
    claimed_effects: list[str]
    subject_types: list[Literal["method", "device", "system", "medium"]] = Field(
        min_length=1
    )
    scope_breadth: Literal["narrow", "medium", "broad"]
    assignee_focus: list[str] = []
    inferred_items: list[str] = []

    @model_validator(mode="after")
    def unique_feature_ids(self) -> "IdeaParserOutput":
        ids = [feature.feature_id for feature in self.features]
        if len(ids) != len(set(ids)):
            raise ValueError("feature IDs must be unique")
        return self


class TermGroup(AgentModel):
    concept: str
    zh_terms: list[str] = Field(min_length=1)
    en_terms: list[str] = Field(min_length=1)
    broader_terms: list[str] = []


class PlannedQuery(AgentModel):
    query_id: str = Field(pattern=r"^Q[1-9][0-9]*$")
    round_number: int = Field(ge=1)
    query_type: Literal[
        "technical_means", "problem_effect", "classification", "distinguishing_feature"
    ]
    language: Literal["zh", "en", "mixed"]
    query_text: str = Field(min_length=3)
    rationale: str = Field(min_length=2)


class QueryPlannerOutput(AgentModel):
    term_groups: list[TermGroup] = Field(min_length=2)
    ipc_cpc_candidates: list[str] = []
    queries: list[PlannedQuery] = Field(min_length=2)

    @model_validator(mode="after")
    def unique_query_ids(self) -> "QueryPlannerOutput":
        ids = [query.query_id for query in self.queries]
        if len(ids) != len(set(ids)):
            raise ValueError("query IDs must be unique")
        if not any(query.query_type == "technical_means" for query in self.queries):
            raise ValueError("query plan requires a technical_means query")
        if not any(query.query_type == "problem_effect" for query in self.queries):
            raise ValueError("query plan requires a problem_effect query")
        return self


class FeatureMapping(AgentModel):
    feature_id: str = Field(pattern=r"^F[1-9][0-9]*$")
    status: Literal["DISCLOSED", "PARTIAL", "NOT_DISCLOSED", "UNCERTAIN"]
    evidence_ids: list[str] = []
    rationale: str = Field(min_length=2)
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def disclosed_requires_evidence(self) -> "FeatureMapping":
        if self.status in {"DISCLOSED", "PARTIAL"} and not self.evidence_ids:
            raise ValueError("disclosed or partial mapping requires evidence IDs")
        return self


class DocumentAnalyzerOutput(AgentModel):
    publication_number: str = Field(min_length=3)
    technical_problem: str
    technical_solution: str
    technical_effect: str
    application_scenario: str
    independent_claim_summary: str
    relevance: Literal["HIGH", "MEDIUM", "LOW"]
    relevance_rationale: str
    feature_mappings: list[FeatureMapping] = Field(min_length=1)
    limitations: list[str] = []

    @model_validator(mode="after")
    def unique_features(self) -> "DocumentAnalyzerOutput":
        ids = [mapping.feature_id for mapping in self.feature_mappings]
        if len(ids) != len(set(ids)):
            raise ValueError("document mappings must have unique feature IDs")
        return self


class NoveltyDocumentMatrix(AgentModel):
    publication_number: str
    mappings: list[FeatureMapping] = Field(min_length=1)
    destroys_novelty: bool

    @model_validator(mode="after")
    def enforce_single_document_coverage(self) -> "NoveltyDocumentMatrix":
        all_disclosed = all(mapping.status == "DISCLOSED" for mapping in self.mappings)
        if self.destroys_novelty != all_disclosed:
            raise ValueError("destroying document must disclose every feature by itself")
        return self


class NoveltyResult(AgentModel):
    conclusion: Literal["NOVEL", "NOT_NOVEL", "UNCERTAIN"]
    confidence: float = Field(ge=0, le=1)
    matrices: list[NoveltyDocumentMatrix] = Field(min_length=1)
    destroying_publication_number: str | None = None
    closest_publication_number: str
    missing_features: list[str]
    rationale: str
    limitations: list[str] = []

    @model_validator(mode="after")
    def conclusion_matches_matrices(self) -> "NoveltyResult":
        destroying = [matrix for matrix in self.matrices if matrix.destroys_novelty]
        if self.conclusion == "NOT_NOVEL":
            if not destroying:
                raise ValueError("NOT_NOVEL requires an identified destroying document")
            destroying_publications = {
                matrix.publication_number for matrix in destroying
            }
            if self.destroying_publication_number not in destroying_publications:
                raise ValueError("destroying publication does not match matrix")
        elif self.conclusion == "NOVEL":
            if destroying or self.destroying_publication_number:
                raise ValueError("NOVEL cannot include a destroying document")
            if not self.missing_features:
                raise ValueError("NOVEL must identify missing required features")
        return self


class DistinguishingFeatureAnalysis(AgentModel):
    feature_id: str
    d2_publication_numbers: list[str] = []
    evidence_ids: list[str] = []
    motivation_to_combine: Literal["YES", "NO", "UNCERTAIN"]
    rationale: str


class InventiveStepOutput(AgentModel):
    route_id: str
    d1_publication_number: str
    distinguishing_features: list[DistinguishingFeatureAnalysis] = Field(min_length=1)
    objective_technical_problem: str
    status: Literal["INVENTIVE", "NOT_INVENTIVE", "UNCERTAIN", "NEED_MORE_EVIDENCE"]
    overall_rationale: str
    limitations: list[str] = []

    @model_validator(mode="after")
    def evidence_matches_status(self) -> "InventiveStepOutput":
        if self.status == "NOT_INVENTIVE":
            for feature in self.distinguishing_features:
                if (
                    not feature.d2_publication_numbers
                    or not feature.evidence_ids
                    or feature.motivation_to_combine != "YES"
                ):
                    raise ValueError(
                        "NOT_INVENTIVE requires D2 evidence and motivation for every feature"
                    )
        return self


class ValueDimension(AgentModel):
    rating: int = Field(ge=1, le=5)
    rationale: str
    evidence_basis: list[str] = []


class ValueAnalyzerOutput(AgentModel):
    detectability: ValueDimension
    workaround_difficulty: ValueDimension
    technical_market_value: ValueDimension
    alternative_paths: list[str] = Field(min_length=2)
    recommendation: Literal["FILE", "ADJUST_THEN_FILE", "WATCH", "DO_NOT_FILE"]
    rationale: str
    limitations: list[str] = []


class AuditIssue(AgentModel):
    severity: Literal["critical", "warning", "info"]
    code: str
    message: str
    evidence_ids: list[str] = []


class EvidenceAuditorOutput(AgentModel):
    issues: list[AuditIssue]
    checked_evidence_ids: list[str]
    checked_publication_numbers: list[str]


class ReportComposerOutput(AgentModel):
    executive_summary: str
    novelty_statement: str
    inventive_step_statement: str
    value_statement: str
    simulated_office_action: str
    action_recommendations: list[str]


AGENT_OUTPUT_MODELS: dict[str, type[AgentModel]] = {
    "patent-idea-parser": IdeaParserOutput,
    "patent-query-planner": QueryPlannerOutput,
    "patent-document-analyzer": DocumentAnalyzerOutput,
    "patent-inventive-step-analyzer": InventiveStepOutput,
    "patent-value-analyzer": ValueAnalyzerOutput,
    "patent-evidence-auditor": EvidenceAuditorOutput,
    "patent-report-composer": ReportComposerOutput,
}


def validate_agent_output(agent_name: str, value: dict[str, Any]) -> AgentModel:
    try:
        model = AGENT_OUTPUT_MODELS[agent_name]
    except KeyError as exc:
        raise ValueError(f"unknown agent: {agent_name}") from exc
    return model.model_validate(value)


def agent_json_schema(agent_name: str) -> dict[str, Any]:
    try:
        return AGENT_OUTPUT_MODELS[agent_name].model_json_schema()
    except KeyError as exc:
        raise ValueError(f"unknown agent: {agent_name}") from exc
