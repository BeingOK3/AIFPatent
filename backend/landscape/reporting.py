from __future__ import annotations

import csv
import io
import re
from collections import Counter
from datetime import date
from typing import Any

from idea.providers.base import FetchedDocument

from .database import LandscapeDatabase
from .schemas import LandscapeClusterPlan, LandscapePatentAnalysis
from .store import LandscapeRunStore, sha256_file


def build_report(
    *,
    run: dict[str, Any],
    coverage: dict[str, Any],
    documents: dict[str, FetchedDocument],
    analyses: dict[str, LandscapePatentAnalysis],
    clusters: LandscapeClusterPlan | None,
    failures: dict[str, str],
    limitations: list[dict[str, Any]],
) -> dict[str, Any]:
    filing_trend: Counter[str] = Counter()
    publication_jurisdictions: Counter[str] = Counter()
    patents = []
    for publication, analysis in analyses.items():
        document = documents[publication]
        filing_month = _month(document.filing_date)
        if filing_month:
            filing_trend[filing_month] += 1
        jurisdiction = _jurisdiction(publication)
        if jurisdiction:
            publication_jurisdictions[jurisdiction] += 1
        patents.append(
            {
                "publication_number": publication,
                "application_number": document.application_number,
                "title": document.title,
                "filing_date": document.filing_date,
                "publication_date": document.publication_date,
                "current_assignee": document.assignee,
                "current_assignee_source": document.provider if document.assignee else None,
                "family_id": document.family_id,
                "family_data_status": "PARTIAL" if document.family_id else "UNAVAILABLE",
                "confirmed_family_jurisdictions": [jurisdiction] if jurisdiction else [],
                "analysis": analysis.model_dump(mode="json"),
            }
        )
    patents.sort(key=lambda item: (item["publication_date"] or "", item["publication_number"]), reverse=True)
    return {
        "schema_version": "landscape-report/1.0.0",
        "run_id": run["run_id"],
        "scope": run["scope_json"],
        "model": run["model"],
        "coverage": coverage,
        "summary": {
            "candidate_count": coverage.get("unique_candidate_count", 0),
            "analyzed_count": len(analyses),
            "failed_analysis_count": len(failures),
            "filing_date_trend": dict(sorted(filing_trend.items())),
            "publication_jurisdictions": dict(sorted(publication_jurisdictions.items())),
            "cluster_count": len(clusters.clusters) if clusters else 0,
        },
        "clusters": clusters.model_dump(mode="json")["clusters"] if clusters else [],
        "patents": patents,
        "failures": failures,
        "limitations": limitations,
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# 专利态势分析报告",
        "",
        f"- Run：`{report['run_id']}`",
        f"- 候选专利：{summary['candidate_count']}",
        f"- 成功精读：{summary['analyzed_count']}",
        f"- 技术聚类：{summary['cluster_count']}",
        "",
        "## 申请日趋势",
        "",
    ]
    trend = summary["filing_date_trend"]
    lines.extend(f"- {month}：{count}" for month, count in trend.items())
    if not trend:
        lines.append("- 无可用申请日数据")
    lines.extend(["", "## 国家/地区布局", ""])
    jurisdictions = summary["publication_jurisdictions"]
    lines.extend(f"- {country}：{count}" for country, count in jurisdictions.items())
    if not jurisdictions:
        lines.append("- 无可用公开号法域数据")
    lines.extend(["", "## 技术聚类", ""])
    for cluster in report["clusters"]:
        lines.extend(
            [
                f"### {cluster['name']}",
                "",
                cluster["summary"],
                "",
                "、".join(cluster["publication_numbers"]),
                "",
            ]
        )
    if not report["clusters"]:
        lines.extend(["- 未形成聚类", ""])
    lines.extend(["## 逐件精读", ""])
    for patent in report["patents"]:
        analysis = patent["analysis"]
        lines.extend(
            [
                f"### {patent['publication_number']} {patent['title']}",
                "",
                f"- 申请号：{patent['application_number'] or '未知'}",
                f"- 申请日：{patent['filing_date'] or '未知'}",
                f"- 公开日：{patent['publication_date'] or '未知'}",
                f"- 当前权利人：{patent['current_assignee'] or '未知'}",
                f"- 同族数据：{patent['family_data_status']}",
                "",
                f"**现有技术：** {analysis['prior_art']}",
                "",
                "**现有技术问题：** " + "；".join(analysis["prior_art_problems"]),
                "",
                "**核心技术发明点：** " + "；".join(analysis["core_invention_points"]),
                "",
                "**解决的技术问题：** " + "；".join(analysis["technical_problems_solved"]),
                "",
                "**有益效果：** " + "；".join(analysis["beneficial_effects"]),
                "",
            ]
        )
    lines.extend(["## 限制与失败", ""])
    for limitation in report["limitations"]:
        lines.append(f"- {limitation.get('code', 'LIMITATION')}：{limitation.get('message', '')}")
    for publication, error in report["failures"].items():
        lines.append(f"- {publication}：{error}")
    if not report["limitations"] and not report["failures"]:
        lines.append("- 无")
    return "\n".join(lines).rstrip() + "\n"


def render_patents_csv(report: dict[str, Any]) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        [
            "publication_number", "application_number", "title", "filing_date",
            "publication_date", "current_assignee", "family_data_status",
            "core_invention_points", "beneficial_effects",
        ]
    )
    for patent in report["patents"]:
        writer.writerow(
            [
                patent["publication_number"], patent["application_number"] or "",
                patent["title"], patent["filing_date"] or "", patent["publication_date"] or "",
                patent["current_assignee"] or "", patent["family_data_status"],
                "；".join(patent["analysis"]["core_invention_points"]),
                "；".join(patent["analysis"]["beneficial_effects"]),
            ]
        )
    return "\ufeff" + output.getvalue()


class LandscapeReportService:
    def __init__(self, database: LandscapeDatabase, store: LandscapeRunStore):
        self.database = database
        self.store = store

    def save(self, run_id: str, report: dict[str, Any]) -> dict[str, Any]:
        markdown = render_markdown(report)
        csv_content = render_patents_csv(report)
        manifest = self.store.write_reports(
            run_id,
            report=report,
            markdown=markdown,
            patents_csv=csv_content,
            manifest_metadata={"schema_version": report["schema_version"]},
        )
        paths = self.store.paths(run_id)
        self.database.put_report_record(
            run_id,
            report_json_path=str(paths.report_json),
            report_json_hash=sha256_file(paths.report_json),
            report_md_path=str(paths.report_md),
            report_md_hash=sha256_file(paths.report_md),
            patents_csv_path=str(paths.patents_csv),
            patents_csv_hash=sha256_file(paths.patents_csv),
            manifest_path=str(paths.manifest),
        )
        return manifest


def _month(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10]).strftime("%Y-%m")
    except ValueError:
        return None


def _jurisdiction(publication_number: str) -> str | None:
    match = re.match(r"^([A-Z]{2})", publication_number.upper())
    return match.group(1) if match else None
