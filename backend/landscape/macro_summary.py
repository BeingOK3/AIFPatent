from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import Field

from .metrics import MetricCube
from .organization_assignment import OrganizationAssignmentSet
from .scope import ScopeModel
from .trends import TrendCandidate, TrendChangeType


class MacroPulsePoint(ScopeModel):
    bucket_id: str
    label: str
    analysis_unit_count: int = Field(ge=0)


class MacroDirectionRow(ScopeModel):
    direction_id: str
    name: str
    analysis_unit_count: int = Field(ge=1)
    change_type: str


class MacroOrganizationRow(ScopeModel):
    organization_id: str
    name: str
    analysis_unit_count: int = Field(ge=1)


class MacroSummary(ScopeModel):
    """Program-owned macro-level summary over the whole landscape window."""

    total_publications: int = Field(ge=0)
    total_analysis_units: int = Field(ge=0)
    direction_count: int = Field(ge=0)
    organization_count: int = Field(ge=0)
    pulse_direction: Literal["GROWING", "STABLE", "DECLINING", "UNCERTAIN"]
    overall_pulse: tuple[MacroPulsePoint, ...]
    top_directions: tuple[MacroDirectionRow, ...]
    top_organizations: tuple[MacroOrganizationRow, ...]
    change_distribution: dict[str, int]
    narrative: str


_PULSE_ZH = {
    "GROWING": "整体呈增长态势",
    "STABLE": "整体保持平稳",
    "DECLINING": "整体呈回落态势",
    "UNCERTAIN": "样本不足以推断整体增减，仅描述当前布局",
}
_CHANGE_ZH = {
    TrendChangeType.NEW: "新出现",
    TrendChangeType.SUSTAINED_ACTIVE: "持续活跃",
    TrendChangeType.STRENGTHENING: "增强",
    TrendChangeType.WEAKENING: "减弱",
    TrendChangeType.CURRENT_LAYOUT: "当前布局",
}


def classify_pulse(counts: list[int], *, minimum_total: int = 5) -> str:
    """Label the aggregated whole-window time series without inventing trends."""
    if minimum_total < 1:
        raise ValueError("minimum_total must be at least 1")
    total = sum(counts)
    if total < minimum_total or len(counts) < 2:
        return "UNCERTAIN"
    midpoint = len(counts) // 2
    first_half = sum(counts[:midpoint])
    second_half = sum(counts[midpoint:])
    if second_half > first_half * 1.5:
        return "GROWING"
    if first_half > second_half * 1.5:
        return "DECLINING"
    return "STABLE"


def build_macro_summary(
    *,
    cube: MetricCube,
    trends: tuple[TrendCandidate, ...],
    organizations: OrganizationAssignmentSet,
    direction_name_by_id: dict[str, str],
    minimum_pulse_total: int = 5,
    top_n: int = 8,
) -> MacroSummary:
    if top_n < 1:
        raise ValueError("top_n must be at least 1")
    bucket_by_id = {bucket.bucket_id: bucket for bucket in cube.buckets}
    pulse_counts = Counter({bucket.bucket_id: 0 for bucket in cube.buckets})
    direction_units: Counter[str] = Counter()
    organization_units: Counter[str] = Counter()
    for cell in cube.cells:
        pulse_counts[cell.bucket_id] += cell.analysis_unit_count
        direction_units[cell.direction_id] += cell.analysis_unit_count
        organization_units[cell.organization_id] += cell.analysis_unit_count

    overall_pulse = tuple(
        MacroPulsePoint(
            bucket_id=bucket.bucket_id,
            label=bucket.label,
            analysis_unit_count=pulse_counts[bucket.bucket_id],
        )
        for bucket in cube.buckets
    )
    pulse_direction = classify_pulse(
        [point.analysis_unit_count for point in overall_pulse],
        minimum_total=minimum_pulse_total,
    )

    trends_by_direction = {trend.direction_id: trend for trend in trends}
    top_directions = tuple(
        MacroDirectionRow(
            direction_id=direction_id,
            name=direction_name_by_id.get(direction_id, direction_id),
            analysis_unit_count=count,
            change_type=(
                trends_by_direction[direction_id].change_type.value
                if direction_id in trends_by_direction
                else TrendChangeType.CURRENT_LAYOUT.value
            ),
        )
        for direction_id, count in sorted(
            direction_units.items(),
            key=lambda item: (-item[1], item[0]),
        )[:top_n]
    )

    org_name_by_id = {
        item.organization_id: item.display_name for item in organizations.organizations
    }
    top_organizations = tuple(
        MacroOrganizationRow(
            organization_id=organization_id,
            name=org_name_by_id.get(organization_id, organization_id),
            analysis_unit_count=count,
        )
        for organization_id, count in sorted(
            organization_units.items(),
            key=lambda item: (-item[1], item[0]),
        )[:top_n]
    )

    change_distribution = {
        change.value: count
        for change, count in sorted(
            Counter(trend.change_type for trend in trends).items(),
            key=lambda item: (-item[1], item[0].value),
        )
    }
    narrative = _narrative(
        summary=MacroSummary(
            total_publications=cube.publication_count,
            total_analysis_units=cube.analysis_unit_count,
            direction_count=len(direction_units),
            organization_count=len(organization_units),
            pulse_direction=pulse_direction,
            overall_pulse=overall_pulse,
            top_directions=top_directions,
            top_organizations=top_organizations,
            change_distribution=change_distribution,
            narrative="",
        )
    )
    return MacroSummary(
        total_publications=cube.publication_count,
        total_analysis_units=cube.analysis_unit_count,
        direction_count=len(direction_units),
        organization_count=len(organization_units),
        pulse_direction=pulse_direction,
        overall_pulse=overall_pulse,
        top_directions=top_directions,
        top_organizations=top_organizations,
        change_distribution=change_distribution,
        narrative=narrative,
    )


def _narrative(summary: MacroSummary) -> str:
    parts = [
        f"窗口内共识别 {summary.direction_count} 个技术方向、"
        f"{summary.organization_count} 家机构、{summary.total_analysis_units} 个分析单元"
        f"（{summary.total_publications} 件公开文本）。",
        _PULSE_ZH[summary.pulse_direction] + "。",
    ]
    if summary.change_distribution:
        distribution = "、".join(
            f"{_CHANGE_ZH.get(TrendChangeType(key), key)} {count}"
            for key, count in summary.change_distribution.items()
        )
        parts.append(f"变化类型分布：{distribution}。")
    else:
        parts.append("样本不足，未形成趋势候选，仅描述当前布局。")
    if summary.top_directions:
        top = summary.top_directions[0]
        parts.append(
            f"最活跃方向：{top.name}（{top.analysis_unit_count} 个分析单元）。"
        )
    return "".join(parts)


__all__ = [
    "MacroDirectionRow",
    "MacroOrganizationRow",
    "MacroPulsePoint",
    "MacroSummary",
    "build_macro_summary",
    "classify_pulse",
]
