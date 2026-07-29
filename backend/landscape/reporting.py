from __future__ import annotations

import csv
import io
import re
from collections import Counter
from typing import Any

from idea.providers.base import FetchedDocument

from .database import LandscapeDatabase
from .schemas import (
    CompanyTechnologyProfile,
    CrossCompanyTrendAnalysis,
    LandscapePatentAnalysis,
)
from .store import LandscapeRunStore, sha256_file


def build_report(
    *,
    run: dict[str, Any],
    coverage: dict[str, Any],
    documents: dict[str, FetchedDocument],
    analyses: dict[str, LandscapePatentAnalysis],
    failures: dict[str, str],
    limitations: list[dict[str, Any]],
    searched_competitor_aliases: list[dict[str, Any]] | None = None,
    technical_direction_expansion: dict[str, Any] | None = None,
    company_profiles: dict[str, CompanyTechnologyProfile] | None = None,
    cross_company_analysis: CrossCompanyTrendAnalysis | None = None,
    company_trend_coverage: dict[str, Any] | None = None,
    company_trend_coverage_history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    # Keep the frozen search metrics and add explicit downstream counters so a
    # report can explain where the complete eligible set stopped being covered.
    coverage = dict(coverage)
    eligible_count = int(
        coverage.get("unique_candidate_count", coverage.get("unique_family_count", 0))
    )
    fetched_count = len(documents)
    analyzed_count = len(analyses)
    company_assigned_count = sum(
        int(item.get("patent_count", 0))
        for item in coverage.get("company_patent_counts", [])
    )
    classified_count = len(
        {
            publication
            for profile in (company_profiles or {}).values()
            for category in profile.technology_categories
            for publication in category.publication_numbers
        }
    )
    coverage.update(
        {
            "unique_eligible_count": eligible_count,
            "company_assigned_count": company_assigned_count,
            "fetch_succeeded_count": fetched_count,
            "analysis_attempted_count": fetched_count,
            "analysis_succeeded_count": analyzed_count,
            "company_classified_count": classified_count,
            "unclassified_publications": sorted(
                set(documents) - set(analyses)
            ),
        }
    )
    publication_jurisdictions: Counter[str] = Counter()
    patents = []
    for publication, analysis in analyses.items():
        document = documents[publication]
        jurisdiction = _jurisdiction(publication)
        if jurisdiction:
            publication_jurisdictions[jurisdiction] += 1
        family_status = _family_status(document)
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
                "family_status": family_status,
                "family_data_status": family_status["data_status"],
                "confirmed_family_jurisdictions": family_status["jurisdictions"],
                "analysis": analysis.model_dump(mode="json"),
            }
        )
    patents.sort(key=lambda item: (item["publication_date"] or "", item["publication_number"]), reverse=True)
    return {
        "schema_version": "landscape-report/2.0.0",
        "run_id": run["run_id"],
        "scope": run["scope_json"],
        "model": run["model"],
        "searched_competitor_aliases": searched_competitor_aliases or [],
        "technical_direction_expansion": technical_direction_expansion,
        "coverage": coverage,
        "summary": {
            "candidate_count": coverage.get("unique_candidate_count", 0),
            "family_count": coverage.get(
                "unique_family_count",
                coverage.get("unique_candidate_count", 0),
            ),
            "publication_count": coverage.get(
                "unique_publication_count",
                coverage.get("unique_candidate_count", 0),
            ),
            "analyzed_count": len(analyses),
            "failed_analysis_count": len(failures),
            "company_patent_counts": coverage.get("company_patent_counts", []),
            "publication_jurisdictions": dict(sorted(publication_jurisdictions.items())),
            "company_profile_count": len(company_profiles or {}),
            "trend_count": len(cross_company_analysis.trends)
            if cross_company_analysis
            else 0,
            "company_trend_coverage_decision": (
                company_trend_coverage.get("decision")
                if company_trend_coverage
                else None
            ),
            "company_trend_coverage_ratio": (
                company_trend_coverage.get("coverage_ratio")
                if company_trend_coverage
                else None
            ),
        },
        "company_profiles": [
            {
                "company_id": company_id,
                **profile.model_dump(mode="json"),
            }
            for company_id, profile in sorted((company_profiles or {}).items())
        ],
        "cross_company_analysis": (
            cross_company_analysis.model_dump(mode="json")
            if cross_company_analysis
            else None
        ),
        "company_trend_coverage": company_trend_coverage,
        "company_trend_coverage_history": company_trend_coverage_history or [],
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
        f"- 唯一合格专利族：{summary.get('family_count', summary['candidate_count'])}",
        f"- 合格公开文本：{summary.get('publication_count', summary['candidate_count'])}",
        f"- 成功精读专利族：{summary['analyzed_count']}",
        "",
        "## 公司趋势覆盖审计",
        "",
    ]
    audit = report.get("company_trend_coverage")
    if audit:
        lines.append(
            "- 决策：{decision}；覆盖率：{ratio:.2%}；修复轮次：{round}".format(
                decision=audit.get("decision", "UNKNOWN"),
                ratio=float(audit.get("coverage_ratio", 0)),
                round=audit.get("repair_round", 0),
            )
        )
        for limitation in audit.get("limitations", []):
            lines.append(f"- 审计限制：{limitation}")
        history = report.get("company_trend_coverage_history", [])
        if history:
            lines.append(
                "- 审计轨迹："
                + " → ".join(
                    f"round {item.get('repair_round', index)} {item.get('decision', 'UNKNOWN')}"
                    for index, item in enumerate(history)
                )
            )
    else:
        lines.append("- 本报告来自旧版本 Run，未记录公司趋势覆盖审计。")
    lines.extend(["", "## 公司专利族数量", ""])
    company_counts = summary.get("company_patent_counts", [])
    lines.extend(
        f"- {item['company']}：{item['patent_count']}"
        for item in company_counts
    )
    if not company_counts:
        lines.append("- 无可用权利人数据")
    lines.extend(["", "## 公司技术画像", ""])
    for company in report.get("company_profiles", []):
        lines.extend(
            [
                f"### {company['company_id']}",
                "",
                company["overall_summary"],
                "",
                "- 技术方向：" + "、".join(company["technology_directions"]),
                "",
            ]
        )
        for category in company["technology_categories"]:
            lines.append(
                f"- {category['name']}：{category['summary']}（"
                + "、".join(category["publication_numbers"])
                + "）"
            )
    if not report.get("company_profiles"):
        lines.append("- 暂无公司技术画像")
    cross_company = report.get("cross_company_analysis")
    lines.extend(["", "## 跨公司整体技术趋势", ""])
    if cross_company:
        lines.extend(
            [
                cross_company["overall_summary"],
                "",
                "- 共同方向：" + "、".join(cross_company["common_directions"]),
                "- 差异方向："
                + "；".join(cross_company["differentiated_directions"]),
                "",
            ]
        )
        for trend in cross_company["trends"]:
            lines.append(
                f"- {trend['trend_id']} {trend['name']}（{trend['direction']}）："
                + trend["summary"]
            )
    else:
        lines.append("- 暂无跨公司趋势")
    lines.extend(["", "## 国家/地区布局", ""])
    jurisdictions = summary["publication_jurisdictions"]
    lines.extend(f"- {country}：{count}" for country, count in jurisdictions.items())
    if not jurisdictions:
        lines.append("- 无可用公开号法域数据")
    lines.extend(["## 逐件精读", ""])
    for patent in report["patents"]:
        analysis = patent["analysis"]
        family = patent.get("family_status", {})
        lines.extend(
            [
                f"### {patent['publication_number']} {patent['title']}",
                "",
                f"- 申请号：{patent['application_number'] or '未知'}",
                f"- 申请日：{patent['filing_date'] or '未知'}",
                f"- 公开日：{patent['publication_date'] or '未知'}",
                f"- 当前权利人：{patent['current_assignee'] or '未知'}",
                f"- 全族数据：{family.get('data_status', patent['family_data_status'])}",
                f"- 全族总体状态：{family.get('overall_legal_status', 'UNKNOWN')}",
                f"- 全族法域：{'、'.join(family.get('jurisdictions', [])) or '未知'}",
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
        for member in family.get("members", []):
            lines.append(
                "- 同族成员：{application}｜{jurisdiction}｜{status}｜申请日 {filing_date}".format(
                    application=member.get("application_number")
                    or member.get("publication_number")
                    or "未知编号",
                    jurisdiction=member.get("jurisdiction") or "未知法域",
                    status=_member_status(member),
                    filing_date=member.get("filing_date") or "未知",
                )
            )
        if family.get("members"):
            lines.append("")
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
            "family_overall_legal_status", "family_jurisdictions", "family_members",
            "core_invention_points", "beneficial_effects",
        ]
    )
    for patent in report["patents"]:
        writer.writerow(
            [
                patent["publication_number"], patent["application_number"] or "",
                patent["title"], patent["filing_date"] or "", patent["publication_date"] or "",
                patent["current_assignee"] or "", patent["family_data_status"],
                patent.get("family_status", {}).get("overall_legal_status", "UNKNOWN"),
                "；".join(patent.get("family_status", {}).get("jurisdictions", [])),
                "；".join(
                    "{application_number}|{jurisdiction}|{legal_status}".format(
                        application_number=item.get("application_number")
                        or item.get("publication_number")
                        or "",
                        jurisdiction=item.get("jurisdiction") or "",
                        legal_status=_member_status(item),
                    )
                    for item in patent.get("family_status", {}).get("members", [])
                ),
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


def _jurisdiction(publication_number: str) -> str | None:
    match = re.match(r"^([A-Z]{2})", publication_number.upper())
    return match.group(1) if match else None


def _family_status(document: FetchedDocument) -> dict[str, Any]:
    raw_applications = document.raw_metadata.get("worldwide_applications") or {}
    members: list[dict[str, Any]] = []
    if isinstance(raw_applications, dict):
        for year, applications in raw_applications.items():
            if not isinstance(applications, list):
                continue
            for application in applications:
                if not isinstance(application, dict):
                    continue
                jurisdiction = (
                    application.get("country_code")
                    or application.get("jurisdiction")
                    or application.get("country")
                )
                legal_status_category = (
                    application.get("legal_status_category")
                    or application.get("legal_status_cat")
                )
                legal_status = (
                    application.get("legal_status")
                    or application.get("status")
                    or legal_status_category
                )
                members.append(
                    {
                        "jurisdiction": str(jurisdiction) if jurisdiction else None,
                        "application_number": application.get("application_number"),
                        "publication_number": application.get("publication_number"),
                        "filing_date": application.get("filing_date"),
                        "year": str(year),
                        "legal_status_category": str(legal_status_category).upper()
                        if legal_status_category
                        else "UNKNOWN",
                        "legal_status": str(legal_status).upper()
                        if legal_status
                        else "UNKNOWN",
                        "is_current_application": _same_identifier(
                            application.get("application_number"),
                            document.application_number,
                        ),
                    }
                )
    fallback_jurisdiction = _jurisdiction(document.publication_number)
    jurisdictions = sorted(
        {
            member["jurisdiction"]
            for member in members
            if member.get("jurisdiction")
        }
        | ({fallback_jurisdiction} if fallback_jurisdiction else set())
    )
    statuses = {
        " ".join(
            (
                member["legal_status_category"],
                member["legal_status"],
            )
        )
        for member in members
        if member["legal_status"] != "UNKNOWN"
        or member["legal_status_category"] != "UNKNOWN"
    }
    active = any(
        marker in status
        for status in statuses
        for marker in ("ACTIVE", "PENDING", "GRANTED")
    )
    inactive = any(
        marker in status
        for status in statuses
        for marker in ("INACTIVE", "EXPIRED", "ABANDONED", "REVOKED", "LAPSED", "DEAD")
    )
    if active and inactive:
        overall = "MIXED"
    elif active:
        overall = "ACTIVE"
    elif inactive:
        overall = "INACTIVE"
    else:
        overall = "UNKNOWN"
    has_family_data = bool(members)
    return {
        "data_status": "PARTIAL" if has_family_data else "UNAVAILABLE",
        "coverage_note": (
            "仅展示当前数据源返回的已确认同族成员，不能据此认定全球同族完整。"
            if has_family_data
            else "当前数据源未返回可核验的同族信息。"
        ),
        "family_id": document.family_id,
        "overall_legal_status": overall,
        "jurisdictions": jurisdictions,
        "members": sorted(
            members,
            key=lambda item: (
                item.get("jurisdiction") or "",
                item.get("application_number") or "",
            ),
        ),
    }


def _same_identifier(left: Any, right: Any) -> bool:
    normalize = lambda value: "".join(
        character for character in str(value or "").upper() if character.isalnum()
    )
    normalized_left = normalize(left)
    return bool(normalized_left and normalized_left == normalize(right))


def _member_status(member: dict[str, Any]) -> str:
    category = member.get("legal_status_category")
    if category and category != "UNKNOWN":
        return str(category)
    return str(member.get("legal_status") or "UNKNOWN")
