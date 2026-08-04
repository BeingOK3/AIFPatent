from __future__ import annotations

import hashlib
import json
from enum import StrEnum

from pydantic import Field, model_validator

from .metrics import MetricAnalysisUnit, MetricCube
from .scope import ScopeModel


TREND_POLICY_VERSION = "landscape-trends/1"


class TrendChangeType(StrEnum):
    NEW = "NEW"
    SUSTAINED_ACTIVE = "SUSTAINED_ACTIVE"
    STRENGTHENING = "STRENGTHENING"
    WEAKENING = "WEAKENING"
    CURRENT_LAYOUT = "CURRENT_LAYOUT"


class ConclusionStrength(StrEnum):
    OBSERVATION = "OBSERVATION"
    STRONG = "STRONG"


class TrendPolicy(ScopeModel):
    version: str = TREND_POLICY_VERSION
    minimum_bucket_count: int = Field(default=3, ge=3, le=100)
    minimum_analysis_units: int = Field(default=6, ge=1, le=10000)
    minimum_terminal_count: int = Field(default=2, ge=1, le=10000)
    change_ratio: float = Field(default=1.5, gt=1, le=100)
    minimum_absolute_change: int = Field(default=2, ge=1, le=10000)


class TrendBucketMetric(ScopeModel):
    bucket_id: str
    analysis_unit_count: int = Field(ge=0)
    publication_count: int = Field(ge=0)


class TrendCandidate(ScopeModel):
    candidate_id: str = Field(pattern=r"^TC-[0-9a-f]{16}$")
    policy_version: str
    direction_id: str
    organization_ids: tuple[str, ...] = Field(min_length=1)
    bucket_metrics: tuple[TrendBucketMetric, ...] = Field(min_length=1)
    change_type: TrendChangeType
    allowed_conclusion_strength: ConclusionStrength
    analysis_unit_ids: tuple[str, ...]
    representative_analysis_unit_ids: tuple[str, ...] = Field(max_length=5)
    evidence_ids: tuple[str, ...]
    limitation_codes: tuple[str, ...]

    @model_validator(mode="after")
    def validate_members(self) -> "TrendCandidate":
        for values, label in (
            (self.organization_ids, "organization"),
            (self.analysis_unit_ids, "analysis unit"),
            (self.representative_analysis_unit_ids, "representative"),
            (self.evidence_ids, "evidence"),
            (self.limitation_codes, "limitation"),
        ):
            if tuple(sorted(set(values))) != values:
                raise ValueError(f"{label} IDs must be unique and sorted")
        if not set(self.representative_analysis_unit_ids).issubset(self.analysis_unit_ids):
            raise ValueError("trend representative is outside candidate members")
        if (
            self.allowed_conclusion_strength == ConclusionStrength.OBSERVATION
            and self.change_type != TrendChangeType.CURRENT_LAYOUT
        ):
            raise ValueError("insufficient evidence cannot claim a strong trend")
        return self


class TrendNarrativeProposal(ScopeModel):
    candidate_id: str = Field(pattern=r"^TC-[0-9a-f]{16}$")
    change_type: TrendChangeType
    conclusion_strength: ConclusionStrength
    narrative: str = Field(min_length=1, max_length=3000)


class TrendNarrative(ScopeModel):
    candidate_id: str
    change_type: TrendChangeType
    conclusion_strength: ConclusionStrength
    narrative: str
    source: str = Field(pattern=r"^(MODEL|DETERMINISTIC)$")


def build_trend_candidates(
    cube: MetricCube,
    units: tuple[MetricAnalysisUnit, ...],
    *,
    policy: TrendPolicy | None = None,
) -> tuple[TrendCandidate, ...]:
    policy = policy or TrendPolicy()
    unit_by_id = {unit.analysis_unit_id: unit for unit in units}
    if len(unit_by_id) != len(units):
        raise ValueError("duplicate trend analysis unit")
    cube_members = {
        member for cell in cube.cells for member in cell.analysis_unit_ids
    }
    unknown = sorted(cube_members - set(unit_by_id))
    if unknown:
        raise ValueError(f"metric cube cites unknown analysis units: {unknown}")
    cell_by_key = {
        (cell.direction_id, cell.organization_id, cell.bucket_id): cell
        for cell in cube.cells
    }
    series_keys = tuple(
        sorted({(cell.direction_id, cell.organization_id) for cell in cube.cells})
    )
    candidates = []
    for direction_id, organization_id in series_keys:
        metrics = tuple(
            TrendBucketMetric(
                bucket_id=bucket.bucket_id,
                analysis_unit_count=(
                    cell_by_key[(direction_id, organization_id, bucket.bucket_id)].analysis_unit_count
                    if (direction_id, organization_id, bucket.bucket_id) in cell_by_key
                    else 0
                ),
                publication_count=(
                    cell_by_key[(direction_id, organization_id, bucket.bucket_id)].publication_count
                    if (direction_id, organization_id, bucket.bucket_id) in cell_by_key
                    else 0
                ),
            )
            for bucket in cube.buckets
        )
        analysis_unit_ids = tuple(
            sorted(
                {
                    member
                    for cell in cube.cells
                    if cell.direction_id == direction_id
                    and cell.organization_id == organization_id
                    for member in cell.analysis_unit_ids
                }
            )
        )
        change_type, strength, limitations = _classify_series(metrics, policy)
        representatives = _select_candidate_representatives(analysis_unit_ids, unit_by_id)
        evidence_ids = tuple(
            sorted(
                {
                    evidence
                    for member_id in analysis_unit_ids
                    for evidence in unit_by_id[member_id].evidence_ids
                }
            )
        )
        identity = {
            "policy": policy.model_dump(mode="json"),
            "cube_hash": cube.cube_hash,
            "direction_id": direction_id,
            "organization_id": organization_id,
            "metrics": [metric.model_dump(mode="json") for metric in metrics],
            "members": analysis_unit_ids,
        }
        candidate_id = f"TC-{_hash(identity)[:16]}"
        candidates.append(
            TrendCandidate(
                candidate_id=candidate_id,
                policy_version=policy.version,
                direction_id=direction_id,
                organization_ids=(organization_id,),
                bucket_metrics=metrics,
                change_type=change_type,
                allowed_conclusion_strength=strength,
                analysis_unit_ids=analysis_unit_ids,
                representative_analysis_unit_ids=representatives,
                evidence_ids=evidence_ids,
                limitation_codes=limitations,
            )
        )
    return tuple(sorted(candidates, key=lambda candidate: candidate.candidate_id))


def apply_trend_narrative(
    candidate: TrendCandidate,
    proposal: TrendNarrativeProposal,
) -> TrendNarrative:
    if proposal.candidate_id != candidate.candidate_id:
        raise ValueError("trend narrative belongs to another candidate")
    if proposal.change_type != candidate.change_type:
        raise ValueError("model cannot change programmatic trend type")
    if (
        candidate.allowed_conclusion_strength == ConclusionStrength.OBSERVATION
        and proposal.conclusion_strength != ConclusionStrength.OBSERVATION
    ):
        raise ValueError("model cannot strengthen an observation")
    return TrendNarrative(
        candidate_id=candidate.candidate_id,
        change_type=candidate.change_type,
        conclusion_strength=proposal.conclusion_strength,
        narrative=proposal.narrative,
        source="MODEL",
    )


def deterministic_trend_narrative(candidate: TrendCandidate) -> TrendNarrative:
    counts = [metric.analysis_unit_count for metric in candidate.bucket_metrics]
    if candidate.change_type == TrendChangeType.CURRENT_LAYOUT:
        narrative = (
            f"当前布局共涉及 {sum(counts)} 个分析单元；由于"
            f"{', '.join(candidate.limitation_codes) or '趋势证据不足'}，不作增长或下降判断。"
        )
    else:
        narrative = (
            f"程序指标将该方向标记为 {candidate.change_type.value}；"
            f"各时间桶分析单元数为 {counts}。"
        )
    return TrendNarrative(
        candidate_id=candidate.candidate_id,
        change_type=candidate.change_type,
        conclusion_strength=candidate.allowed_conclusion_strength,
        narrative=narrative,
        source="DETERMINISTIC",
    )


def _classify_series(
    metrics: tuple[TrendBucketMetric, ...],
    policy: TrendPolicy,
) -> tuple[TrendChangeType, ConclusionStrength, tuple[str, ...]]:
    counts = [metric.analysis_unit_count for metric in metrics]
    limitations = []
    if len(metrics) < policy.minimum_bucket_count:
        limitations.append("INSUFFICIENT_TIME_BUCKETS")
    if sum(counts) < policy.minimum_analysis_units:
        limitations.append("INSUFFICIENT_ANALYSIS_UNITS")
    if limitations:
        return (
            TrendChangeType.CURRENT_LAYOUT,
            ConclusionStrength.OBSERVATION,
            tuple(sorted(limitations)),
        )
    split = max(1, len(counts) // 3)
    early = sum(counts[:split])
    recent = sum(counts[-split:])
    if early == 0 and recent >= policy.minimum_terminal_count:
        change_type = TrendChangeType.NEW
    elif (
        recent - early >= policy.minimum_absolute_change
        and recent >= early * policy.change_ratio
    ):
        change_type = TrendChangeType.STRENGTHENING
    elif (
        early - recent >= policy.minimum_absolute_change
        and early >= recent * policy.change_ratio
    ):
        change_type = TrendChangeType.WEAKENING
    elif sum(count > 0 for count in counts) >= policy.minimum_bucket_count:
        change_type = TrendChangeType.SUSTAINED_ACTIVE
    else:
        return (
            TrendChangeType.CURRENT_LAYOUT,
            ConclusionStrength.OBSERVATION,
            ("NO_STRONG_CHANGE_PATTERN",),
        )
    return change_type, ConclusionStrength.STRONG, ()


def _select_candidate_representatives(
    analysis_unit_ids: tuple[str, ...],
    unit_by_id: dict[str, MetricAnalysisUnit],
) -> tuple[str, ...]:
    ranked = sorted(
        analysis_unit_ids,
        key=lambda member_id: (
            -unit_by_id[member_id].classification_confidence,
            -unit_by_id[member_id].evidence_completeness,
            -unit_by_id[member_id].direction_centrality,
            member_id,
        ),
    )
    return tuple(sorted(ranked[:3]))


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


__all__ = [
    "ConclusionStrength",
    "TREND_POLICY_VERSION",
    "TrendBucketMetric",
    "TrendCandidate",
    "TrendChangeType",
    "TrendNarrative",
    "TrendNarrativeProposal",
    "TrendPolicy",
    "apply_trend_narrative",
    "build_trend_candidates",
    "deterministic_trend_narrative",
]
