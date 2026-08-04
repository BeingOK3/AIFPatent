from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import date

from pydantic import Field, model_validator

from .classification_terminal import ClassificationResult, ClassificationTerminal
from .direction_record import DirectionRecord
from .family_resolution import FamilyResolution
from .macro_summary import MacroSummary, build_macro_summary
from .mode_views import LandscapeModeView, build_mode_view
from .organization_assignment import OrganizationAssignmentSet
from .others_discovery import OthersDiscovery
from .patent_snapshot import PatentSnapshotSet, PatentSnapshotStatus
from .query_planning import V4QueryPlan
from .representatives import RepresentativePatent, google_patents_url
from .scale_repository import ScaleGateRecord
from .scope import ConfirmedScopeRevision, ScopeModel
from .stage_repository import V4RunLimitation
from .trends import TrendCandidate, deterministic_trend_narrative
from .metrics import MetricCube


REPORT_SCHEMA_VERSION = "landscape-report/4.1.0"


class QueryAudit(ScopeModel):
    query_id: str
    query_text: str
    page_count: int = Field(ge=0)
    raw_hit_count: int = Field(ge=0)
    final_stop_reason: str


class ReportCounts(ScopeModel):
    raw_hit_count: int = Field(ge=0)
    frozen_publication_count: int = Field(ge=0)
    analysis_unit_count: int = Field(ge=0)
    detail_fetch_failed_count: int = Field(ge=0)
    classified_count: int = Field(ge=0)
    others_count: int = Field(ge=0)
    unresolved_count: int = Field(ge=0)


class PatentReportRow(ScopeModel):
    publication_id: str
    analysis_unit_id: str
    publication_number: str
    title: str
    publication_date: date | None
    applicants: tuple[str, ...]
    classification_terminal: ClassificationTerminal
    direction_id: str | None
    unresolved_reason: str | None
    patent_url: str | None
    link_target: str = "_blank"
    link_rel: str = "noopener noreferrer"


class LandscapeReportV4(ScopeModel):
    schema_version: str = REPORT_SCHEMA_VERSION
    run_id: str
    workflow_version: str
    scope: ConfirmedScopeRevision
    taxonomy_version: str
    query_plan: V4QueryPlan
    query_audit: tuple[QueryAudit, ...]
    scale_gate: ScaleGateRecord
    counts: ReportCounts
    metric_cube: MetricCube
    mode_view: LandscapeModeView
    macro_summary: MacroSummary
    others: OthersDiscovery
    trends: tuple[TrendCandidate, ...]
    representatives: tuple[RepresentativePatent, ...]
    patents: tuple[PatentReportRow, ...]
    limitations: tuple[V4RunLimitation, ...]
    report_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_report(self) -> "LandscapeReportV4":
        if self.counts.frozen_publication_count != len(self.patents):
            raise ValueError("report patent count mismatch")
        unit_terminals = {
            item.analysis_unit_id: item.classification_terminal for item in self.patents
        }
        if len(unit_terminals) != self.counts.analysis_unit_count:
            raise ValueError("report analysis unit count mismatch")
        terminals = Counter(unit_terminals.values())
        if (
            self.counts.classified_count != terminals[ClassificationTerminal.CLASSIFIED]
            or self.counts.others_count != terminals[ClassificationTerminal.OTHERS]
            or self.counts.unresolved_count != terminals[ClassificationTerminal.UNRESOLVED]
        ):
            raise ValueError("report classification counts mismatch")
        semantic = self.model_dump(mode="json", exclude={"report_hash"})
        if self.report_hash != _hash(semantic):
            raise ValueError("report hash mismatch")
        return self


def build_report_v4(
    *,
    run,
    scope: ConfirmedScopeRevision,
    query_plan: V4QueryPlan,
    search_pages_by_query: dict[str, tuple],
    scale_gate: ScaleGateRecord,
    resolution: FamilyResolution,
    snapshots: PatentSnapshotSet,
    organizations: OrganizationAssignmentSet,
    classifications: tuple[ClassificationResult, ...],
    directions: tuple[DirectionRecord, ...],
    others: OthersDiscovery,
    metric_cube: MetricCube,
    trends: tuple[TrendCandidate, ...],
    representatives: tuple[RepresentativePatent, ...],
    limitations: tuple[V4RunLimitation, ...],
) -> LandscapeReportV4:
    classification_by_id = {item.analysis_unit_id: item for item in classifications}
    direction_by_id = {item.analysis_unit_id: item for item in directions}
    unit_by_publication = {
        publication_id: unit.analysis_unit_id
        for unit in resolution.analysis_units
        for publication_id in unit.member_publication_ids
    }
    others_by_member = {
        member_id: cluster.cluster_id
        for cluster in others.clusters
        for member_id in cluster.member_ids
    }
    assignment_by_id = {
        item.publication_id: item for item in organizations.assignments
    }
    patents = []
    for snapshot in snapshots.snapshots:
        unit_id = unit_by_publication[snapshot.publication_id]
        classification = classification_by_id[unit_id]
        direction = direction_by_id[unit_id]
        direction_id = (
            classification.primary_category_id
            if classification.terminal == ClassificationTerminal.CLASSIFIED
            else others_by_member[unit_id]
            if classification.terminal == ClassificationTerminal.OTHERS
            else None
        )
        patents.append(
            PatentReportRow(
                publication_id=snapshot.publication_id,
                analysis_unit_id=unit_id,
                publication_number=snapshot.publication_number or snapshot.publication_id,
                title=snapshot.title or snapshot.publication_number or snapshot.publication_id,
                publication_date=snapshot.publication_date,
                applicants=assignment_by_id[snapshot.publication_id].observed_applicants,
                classification_terminal=classification.terminal,
                direction_id=direction_id,
                unresolved_reason=(
                    classification.unresolved_reason or direction.unresolved_reason
                    if classification.terminal == ClassificationTerminal.UNRESOLVED
                    else None
                ),
                patent_url=google_patents_url(snapshot.publication_number or ""),
            )
        )
    query_audit = tuple(
        QueryAudit(
            query_id=query.query_id,
            query_text=query.query_text,
            page_count=len(search_pages_by_query.get(query.query_id, ())),
            raw_hit_count=sum(
                len(page.hits) for page in search_pages_by_query.get(query.query_id, ())
            ),
            final_stop_reason=(
                search_pages_by_query[query.query_id][-1].stop_reason.value
                if search_pages_by_query.get(query.query_id)
                else "NO_PAGE"
            ),
        )
        for query in query_plan.queries
    )
    terminal_counts = Counter(item.terminal for item in classifications)
    confirmed_organization_ids = tuple(
        sorted(
            item.organization_id
            for item in organizations.organizations
            if item.source_profile_id is not None
        )
    )
    mode_view = build_mode_view(
        metric_cube,
        mode=scope.mode,
        confirmed_organization_ids=confirmed_organization_ids,
    )
    direction_name_by_id: dict[str, str] = {}
    for representative in representatives:
        if representative.direction_id not in direction_name_by_id and representative.classification_path:
            direction_name_by_id[representative.direction_id] = " / ".join(
                representative.classification_path
            )
    macro_summary = build_macro_summary(
        cube=metric_cube,
        trends=trends,
        organizations=organizations,
        direction_name_by_id=direction_name_by_id,
    )
    semantic = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "run_id": run.run_id,
        "workflow_version": run.workflow_version,
        "scope": scope,
        "taxonomy_version": run.taxonomy_version,
        "query_plan": query_plan,
        "query_audit": query_audit,
        "scale_gate": scale_gate,
        "counts": ReportCounts(
            raw_hit_count=sum(item.raw_hit_count for item in query_audit),
            frozen_publication_count=len(snapshots.snapshots),
            analysis_unit_count=resolution.analysis_unit_count,
            detail_fetch_failed_count=sum(
                item.status == PatentSnapshotStatus.PROVIDER_FAILED
                for item in snapshots.snapshots
            ),
            classified_count=terminal_counts[ClassificationTerminal.CLASSIFIED],
            others_count=terminal_counts[ClassificationTerminal.OTHERS],
            unresolved_count=terminal_counts[ClassificationTerminal.UNRESOLVED],
        ),
        "metric_cube": metric_cube,
        "mode_view": mode_view,
        "macro_summary": macro_summary,
        "others": others,
        "trends": trends,
        "representatives": representatives,
        "patents": tuple(patents),
        "limitations": limitations,
    }
    json_semantic = {
        key: value.model_dump(mode="json") if isinstance(value, ScopeModel)
        else [item.model_dump(mode="json") for item in value]
        if isinstance(value, tuple)
        else value
        for key, value in semantic.items()
    }
    return LandscapeReportV4(**semantic, report_hash=_hash(json_semantic))


def render_report_markdown(report: LandscapeReportV4) -> str:
    scope = report.scope
    lines = [
        "# 专利态势报告",
        "",
        f"- Run：`{report.run_id}`",
        f"- 模式：{scope.mode.value}",
        f"- 公开时间窗：{scope.publication_start.isoformat()} 至 {scope.publication_end.isoformat()}（含首尾）",
        f"- 分类标准：`{report.taxonomy_version}`",
        "",
        "## 完整性与限制",
        "",
        f"原始命中 {report.counts.raw_hit_count}，冻结 Publication {report.counts.frozen_publication_count}，Analysis Unit {report.counts.analysis_unit_count}。",
        f"分类内 {report.counts.classified_count}，Others {report.counts.others_count}，Unresolved {report.counts.unresolved_count}，详情抓取失败 {report.counts.detail_fetch_failed_count}。",
    ]
    for limitation in report.limitations:
        lines.append(f"- {limitation.code}：{limitation.message}")
    macro = report.macro_summary
    lines.extend(["", "## 宏观趋势总结", ""])
    lines.append(macro.narrative)
    pulse = "；".join(
        f"{point.label} {point.analysis_unit_count}" for point in macro.overall_pulse
    )
    lines.append(f"时间分布：{pulse}。")
    if macro.top_directions:
        lines.append(
            "Top 方向："
            + "；".join(
                f"{item.name}（{item.analysis_unit_count}，{item.change_type}）"
                for item in macro.top_directions
            )
        )
    if macro.top_organizations:
        lines.append(
            "Top 机构："
            + "；".join(
                f"{item.name}（{item.analysis_unit_count}）"
                for item in macro.top_organizations
            )
        )
    lines.extend(["", "## 实际查询", ""])
    for query in report.query_audit:
        lines.append(
            f"- `{query.query_id}`：{query.query_text}；{query.page_count} 页，"
            f"{query.raw_hit_count} 条，停止原因 {query.final_stop_reason}"
        )
    lines.extend(["", "## 趋势", ""])
    if report.trends:
        for candidate in report.trends:
            lines.append(f"- {deterministic_trend_narrative(candidate).narrative}")
    else:
        lines.append("当前没有足够的可统计成员形成趋势候选。")
    lines.extend(["", "## 代表专利", ""])
    if report.representatives:
        for item in report.representatives:
            title = item.title.replace("[", "\\[").replace("]", "\\]")
            label = f"[{title}]({item.patent_url})" if item.patent_url else title
            lines.append(
                f"- {label}（{item.publication_number}，{item.publication_date.isoformat()}）"
            )
    else:
        lines.append("当前没有满足规则的代表专利。")
    return "\n".join(lines).rstrip() + "\n"


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


__all__ = [
    "REPORT_SCHEMA_VERSION",
    "LandscapeReportV4",
    "PatentReportRow",
    "QueryAudit",
    "ReportCounts",
    "build_report_v4",
    "render_report_markdown",
]
