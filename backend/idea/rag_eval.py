from __future__ import annotations

import math
from collections.abc import Awaitable, Callable, Sequence
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RagEvalError(RuntimeError):
    """Raised when an evaluation run cannot be compared to its ground truth."""


class StrictEvalModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RagEvalWorkflow(str, Enum):
    INITIAL_REPORT = "INITIAL_REPORT"
    FOLLOWUP = "FOLLOWUP"


class RelevantChunk(StrictEvalModel):
    chunk_id: str = Field(min_length=1)
    version_id: str = Field(min_length=1)
    relevance: int = Field(ge=1, le=3)


class CitationTarget(StrictEvalModel):
    chunk_id: str = Field(min_length=1)
    version_id: str = Field(min_length=1)
    quote_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class RagEvalCase(StrictEvalModel):
    case_id: str = Field(min_length=1)
    workflow: RagEvalWorkflow
    query_text: str = Field(min_length=1)
    allowed_version_ids: tuple[str, ...] = Field(min_length=1)
    relevant_chunks: tuple[RelevantChunk, ...] = Field(min_length=1)
    required_version_ids: tuple[str, ...] = ()
    citation_targets: tuple[CitationTarget, ...] = ()
    notes: str = ""

    @model_validator(mode="after")
    def validate_scope_and_identity(self) -> "RagEvalCase":
        allowed = set(self.allowed_version_ids)
        if len(allowed) != len(self.allowed_version_ids) or any(
            not value.strip() for value in self.allowed_version_ids
        ):
            raise ValueError("allowed Version IDs must be unique and non-empty")
        relevant_ids = [value.chunk_id for value in self.relevant_chunks]
        if len(set(relevant_ids)) != len(relevant_ids):
            raise ValueError("relevant Chunk IDs must be unique")
        if any(value.version_id not in allowed for value in self.relevant_chunks):
            raise ValueError("relevant Chunk escaped the allowed Version scope")
        required = set(self.required_version_ids)
        if len(required) != len(self.required_version_ids) or not required <= allowed:
            raise ValueError("required Version IDs must be a unique subset of scope")
        target_keys = [
            (value.chunk_id, value.version_id, value.quote_hash)
            for value in self.citation_targets
        ]
        if len(set(target_keys)) != len(target_keys):
            raise ValueError("Citation targets must be unique")
        relevant_identity = {
            (value.chunk_id, value.version_id) for value in self.relevant_chunks
        }
        if any(
            (value.chunk_id, value.version_id) not in relevant_identity
            for value in self.citation_targets
        ):
            raise ValueError("Citation target must refer to a relevant Chunk")
        return self


class RagEvalDataset(StrictEvalModel):
    schema_version: Literal["rag-eval-dataset-v1"]
    dataset_id: str = Field(min_length=1)
    judgment_policy: str = Field(min_length=1)
    cases: tuple[RagEvalCase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_cases(self) -> "RagEvalDataset":
        values = [case.case_id for case in self.cases]
        if len(set(values)) != len(values):
            raise ValueError("evaluation case IDs must be unique")
        return self


class ObservedHit(StrictEvalModel):
    chunk_id: str = Field(min_length=1)
    version_id: str = Field(min_length=1)
    rank: int = Field(ge=1)
    score: float | None = None
    sources: tuple[str, ...] = ()

    @model_validator(mode="after")
    def finite_score(self) -> "ObservedHit":
        if self.score is not None and not math.isfinite(self.score):
            raise ValueError("retrieval score must be finite")
        return self


class ObservedCitation(StrictEvalModel):
    chunk_id: str = Field(min_length=1)
    version_id: str = Field(min_length=1)
    quote_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class RagEvalObservation(StrictEvalModel):
    case_id: str = Field(min_length=1)
    hits: tuple[ObservedHit, ...]
    citations: tuple[ObservedCitation, ...] = ()

    @model_validator(mode="after")
    def unique_results(self) -> "RagEvalObservation":
        hit_ids = [value.chunk_id for value in self.hits]
        ranks = [value.rank for value in self.hits]
        if len(set(hit_ids)) != len(hit_ids) or len(set(ranks)) != len(ranks):
            raise ValueError("observed Chunk IDs and ranks must be unique")
        citation_keys = [
            (value.chunk_id, value.version_id, value.quote_hash)
            for value in self.citations
        ]
        if len(set(citation_keys)) != len(citation_keys):
            raise ValueError("observed Citations must be unique")
        return self


class RagEvalRun(StrictEvalModel):
    schema_version: Literal["rag-eval-run-v1"]
    run_id: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    system_version: str = Field(min_length=1)
    retriever_version: str = Field(min_length=1)
    observations: tuple[RagEvalObservation, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_observations(self) -> "RagEvalRun":
        values = [item.case_id for item in self.observations]
        if len(set(values)) != len(values):
            raise ValueError("evaluation observations must have unique case IDs")
        return self


class RagEvalGates(StrictEvalModel):
    min_macro_recall_at_k: float = Field(default=0.0, ge=0.0, le=1.0)
    min_macro_ndcg_at_k: float = Field(default=0.0, ge=0.0, le=1.0)
    min_macro_citation_recall: float = Field(default=0.0, ge=0.0, le=1.0)
    max_scope_violations: int = Field(default=0, ge=0)
    max_citation_violations: int = Field(default=0, ge=0)


class RagEvalCaseMetrics(StrictEvalModel):
    case_id: str
    workflow: RagEvalWorkflow
    relevant_count: int
    retrieved_count_at_k: int
    relevant_retrieved_at_k: int
    precision_at_k: float
    recall_at_k: float
    reciprocal_rank_at_k: float
    ndcg_at_k: float
    version_coverage_at_k: float
    citation_target_count: int
    valid_citation_count: int
    citation_precision: float
    citation_recall: float
    scope_violations: tuple[str, ...]
    citation_violations: tuple[str, ...]


class RagEvalSummary(StrictEvalModel):
    schema_version: Literal["rag-eval-summary-v1"] = "rag-eval-summary-v1"
    dataset_id: str
    run_id: str
    system_version: str
    retriever_version: str
    cutoff_k: int
    case_count: int
    workflow_case_counts: dict[str, int]
    macro_precision_at_k: float
    macro_recall_at_k: float
    macro_reciprocal_rank_at_k: float
    macro_ndcg_at_k: float
    macro_version_coverage_at_k: float
    citation_case_count: int
    macro_citation_precision: float
    macro_citation_recall: float
    scope_violation_count: int
    citation_violation_count: int
    passed: bool
    failures: tuple[str, ...]
    cases: tuple[RagEvalCaseMetrics, ...]


class RagEvalComparison(StrictEvalModel):
    schema_version: Literal["rag-eval-comparison-v1"] = "rag-eval-comparison-v1"
    dataset_id: str
    baseline_run_id: str
    candidate_run_id: str
    cutoff_k: int
    macro_recall_delta: float
    macro_ndcg_delta: float
    macro_citation_recall_delta: float
    scope_violation_delta: int
    citation_violation_delta: int
    passed: bool
    failures: tuple[str, ...]


EvalSearch = Callable[
    [RagEvalCase, int], Awaitable[Sequence[ObservedHit]]
]


async def collect_rag_run(
    dataset: RagEvalDataset,
    search: EvalSearch,
    *,
    run_id: str,
    system_version: str,
    retriever_version: str,
    limit: int = 10,
) -> RagEvalRun:
    """Collect retrieval observations without allowing the collector to change scope."""
    if not all(value.strip() for value in (run_id, system_version, retriever_version)):
        raise RagEvalError("evaluation run coordinates must not be empty")
    if not 1 <= limit <= 100:
        raise RagEvalError("evaluation collection limit must be between 1 and 100")
    observations = []
    for case in dataset.cases:
        hits = tuple(await search(case, limit))
        if any(hit.version_id not in case.allowed_version_ids for hit in hits):
            raise RagEvalError(
                f"collector returned a Chunk outside frozen scope for {case.case_id}"
            )
        observations.append(
            RagEvalObservation(case_id=case.case_id, hits=hits, citations=())
        )
    return RagEvalRun(
        schema_version="rag-eval-run-v1",
        run_id=run_id,
        dataset_id=dataset.dataset_id,
        system_version=system_version,
        retriever_version=retriever_version,
        observations=tuple(observations),
    )


def evaluate_rag_run(
    dataset: RagEvalDataset,
    run: RagEvalRun,
    *,
    cutoff_k: int = 10,
    gates: RagEvalGates | None = None,
) -> RagEvalSummary:
    if cutoff_k < 1:
        raise RagEvalError("evaluation cutoff must be positive")
    if run.dataset_id != dataset.dataset_id:
        raise RagEvalError("evaluation run dataset ID does not match ground truth")
    case_by_id = {case.case_id: case for case in dataset.cases}
    observation_by_id = {item.case_id: item for item in run.observations}
    if set(case_by_id) != set(observation_by_id):
        missing = sorted(set(case_by_id) - set(observation_by_id))
        unexpected = sorted(set(observation_by_id) - set(case_by_id))
        raise RagEvalError(
            f"evaluation case coverage mismatch; missing={missing}, unexpected={unexpected}"
        )

    metrics = tuple(
        _evaluate_case(case_by_id[case_id], observation_by_id[case_id], cutoff_k)
        for case_id in case_by_id
    )
    citation_cases = tuple(item for item in metrics if item.citation_target_count)
    workflow_counts = {
        workflow.value: sum(item.workflow == workflow for item in metrics)
        for workflow in RagEvalWorkflow
    }
    active_gates = gates or RagEvalGates()
    values = {
        "macro_precision_at_k": _mean(item.precision_at_k for item in metrics),
        "macro_recall_at_k": _mean(item.recall_at_k for item in metrics),
        "macro_reciprocal_rank_at_k": _mean(
            item.reciprocal_rank_at_k for item in metrics
        ),
        "macro_ndcg_at_k": _mean(item.ndcg_at_k for item in metrics),
        "macro_version_coverage_at_k": _mean(
            item.version_coverage_at_k for item in metrics
        ),
        "macro_citation_precision": _mean(
            item.citation_precision for item in citation_cases
        ),
        "macro_citation_recall": _mean(
            item.citation_recall for item in citation_cases
        ),
        "scope_violation_count": sum(len(item.scope_violations) for item in metrics),
        "citation_violation_count": sum(
            len(item.citation_violations) for item in metrics
        ),
    }
    failures = _gate_failures(values, active_gates)
    return RagEvalSummary(
        dataset_id=dataset.dataset_id,
        run_id=run.run_id,
        system_version=run.system_version,
        retriever_version=run.retriever_version,
        cutoff_k=cutoff_k,
        case_count=len(metrics),
        workflow_case_counts=workflow_counts,
        citation_case_count=len(citation_cases),
        passed=not failures,
        failures=tuple(failures),
        cases=metrics,
        **values,
    )


def compare_rag_summaries(
    baseline: RagEvalSummary,
    candidate: RagEvalSummary,
    *,
    min_recall_delta: float = 0.0,
    min_ndcg_delta: float = 0.0,
    min_citation_recall_delta: float = 0.0,
) -> RagEvalComparison:
    if baseline.dataset_id != candidate.dataset_id:
        raise RagEvalError("cannot compare summaries from different datasets")
    if baseline.cutoff_k != candidate.cutoff_k:
        raise RagEvalError("cannot compare summaries with different cutoffs")
    deltas = {
        "macro_recall_delta": candidate.macro_recall_at_k - baseline.macro_recall_at_k,
        "macro_ndcg_delta": candidate.macro_ndcg_at_k - baseline.macro_ndcg_at_k,
        "macro_citation_recall_delta": (
            candidate.macro_citation_recall - baseline.macro_citation_recall
        ),
        "scope_violation_delta": (
            candidate.scope_violation_count - baseline.scope_violation_count
        ),
        "citation_violation_delta": (
            candidate.citation_violation_count - baseline.citation_violation_count
        ),
    }
    failures = []
    if deltas["macro_recall_delta"] < min_recall_delta:
        failures.append("MACRO_RECALL_REGRESSION")
    if deltas["macro_ndcg_delta"] < min_ndcg_delta:
        failures.append("MACRO_NDCG_REGRESSION")
    if deltas["macro_citation_recall_delta"] < min_citation_recall_delta:
        failures.append("CITATION_RECALL_REGRESSION")
    if deltas["scope_violation_delta"] > 0:
        failures.append("SCOPE_VIOLATION_REGRESSION")
    if deltas["citation_violation_delta"] > 0:
        failures.append("CITATION_VIOLATION_REGRESSION")
    return RagEvalComparison(
        dataset_id=baseline.dataset_id,
        baseline_run_id=baseline.run_id,
        candidate_run_id=candidate.run_id,
        cutoff_k=baseline.cutoff_k,
        passed=not failures,
        failures=tuple(failures),
        **deltas,
    )


def _evaluate_case(
    case: RagEvalCase, observation: RagEvalObservation, cutoff_k: int
) -> RagEvalCaseMetrics:
    ranked = tuple(sorted(observation.hits, key=lambda item: item.rank))[:cutoff_k]
    relevant = {item.chunk_id: item for item in case.relevant_chunks}
    relevant_hits = tuple(item for item in ranked if item.chunk_id in relevant)
    precision = len(relevant_hits) / len(ranked) if ranked else 0.0
    recall = len(relevant_hits) / len(relevant)
    reciprocal_rank = next(
        (1.0 / index for index, item in enumerate(ranked, start=1) if item.chunk_id in relevant),
        0.0,
    )
    dcg = sum(
        (2 ** relevant[item.chunk_id].relevance - 1) / math.log2(index + 1)
        for index, item in enumerate(ranked, start=1)
        if item.chunk_id in relevant
    )
    ideal = sorted((item.relevance for item in relevant.values()), reverse=True)[:cutoff_k]
    ideal_dcg = sum(
        (2 ** relevance - 1) / math.log2(index + 1)
        for index, relevance in enumerate(ideal, start=1)
    )
    required_versions = set(case.required_version_ids) or {
        item.version_id for item in relevant.values()
    }
    retrieved_relevant_versions = {
        relevant[item.chunk_id].version_id
        for item in relevant_hits
        if relevant[item.chunk_id].version_id in required_versions
    }
    version_coverage = (
        len(retrieved_relevant_versions) / len(required_versions)
        if required_versions
        else 1.0
    )
    allowed = set(case.allowed_version_ids)
    scope_violations = [
        f"HIT_OUT_OF_SCOPE:{item.chunk_id}:{item.version_id}"
        for item in observation.hits
        if item.version_id not in allowed
    ]
    scope_violations.extend(
        f"CITATION_OUT_OF_SCOPE:{item.chunk_id}:{item.version_id}"
        for item in observation.citations
        if item.version_id not in allowed
    )
    retrieved_identity = {(item.chunk_id, item.version_id) for item in observation.hits}
    targets = {
        (item.chunk_id, item.version_id, item.quote_hash)
        for item in case.citation_targets
    }
    valid_citations = 0
    citation_violations = []
    for citation in observation.citations:
        identity = (citation.chunk_id, citation.version_id)
        if identity not in retrieved_identity:
            citation_violations.append(
                f"CITATION_NOT_RETRIEVED:{citation.chunk_id}:{citation.version_id}"
            )
            continue
        if targets and (
            citation.chunk_id, citation.version_id, citation.quote_hash
        ) not in targets:
            citation_violations.append(
                f"CITATION_NOT_GROUND_TRUTH:{citation.chunk_id}:{citation.version_id}"
            )
            continue
        valid_citations += 1
    cited_targets = {
        (item.chunk_id, item.version_id, item.quote_hash)
        for item in observation.citations
        if (item.chunk_id, item.version_id, item.quote_hash) in targets
        and (item.chunk_id, item.version_id) in retrieved_identity
    }
    citation_precision = (
        valid_citations / len(observation.citations)
        if observation.citations
        else (0.0 if targets else 1.0)
    )
    citation_recall = len(cited_targets) / len(targets) if targets else 1.0
    return RagEvalCaseMetrics(
        case_id=case.case_id,
        workflow=case.workflow,
        relevant_count=len(relevant),
        retrieved_count_at_k=len(ranked),
        relevant_retrieved_at_k=len(relevant_hits),
        precision_at_k=precision,
        recall_at_k=recall,
        reciprocal_rank_at_k=reciprocal_rank,
        ndcg_at_k=dcg / ideal_dcg if ideal_dcg else 0.0,
        version_coverage_at_k=version_coverage,
        citation_target_count=len(targets),
        valid_citation_count=valid_citations,
        citation_precision=citation_precision,
        citation_recall=citation_recall,
        scope_violations=tuple(scope_violations),
        citation_violations=tuple(citation_violations),
    )


def _mean(values) -> float:
    items = tuple(values)
    return sum(items) / len(items) if items else 1.0


def _gate_failures(values: dict[str, float | int], gates: RagEvalGates) -> list[str]:
    failures = []
    if values["macro_recall_at_k"] < gates.min_macro_recall_at_k:
        failures.append("MACRO_RECALL_BELOW_GATE")
    if values["macro_ndcg_at_k"] < gates.min_macro_ndcg_at_k:
        failures.append("MACRO_NDCG_BELOW_GATE")
    if values["macro_citation_recall"] < gates.min_macro_citation_recall:
        failures.append("CITATION_RECALL_BELOW_GATE")
    if values["scope_violation_count"] > gates.max_scope_violations:
        failures.append("SCOPE_VIOLATIONS_ABOVE_GATE")
    if values["citation_violation_count"] > gates.max_citation_violations:
        failures.append("CITATION_VIOLATIONS_ABOVE_GATE")
    return failures


__all__ = [
    "CitationTarget",
    "ObservedCitation",
    "ObservedHit",
    "RagEvalCase",
    "RagEvalCaseMetrics",
    "RagEvalComparison",
    "RagEvalDataset",
    "RagEvalError",
    "RagEvalGates",
    "RagEvalObservation",
    "RagEvalRun",
    "RagEvalSummary",
    "RagEvalWorkflow",
    "RelevantChunk",
    "compare_rag_summaries",
    "collect_rag_run",
    "evaluate_rag_run",
]
