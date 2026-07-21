from __future__ import annotations

import json
import re
from typing import Any

from .agent_schemas import (
    IdeaParserOutput,
    InventiveStepOutput,
    NoveltyResult,
    ReportComposerOutput,
    ValueAnalyzerOutput,
)
from .agents import AgentExecutionError, IdeaAgentService
from .database import Database, canonical_json, now_ms
from .citations import VerifiedCitation
from .run_store import RunStore


REPORT_COMPOSER_PROMPT = """
You are patent-report-composer. Write concise Chinese narrative from only the supplied frozen
facts. The supplied conclusion labels, publication numbers, dates and statistics are immutable.
Do not introduce a publication number, date, count, legal conclusion, search result, or market
fact not present in the payload. The novelty_statement must begin with the supplied exact
Chinese novelty label. Recommendations may explain next steps but may not claim a tool was run.
"""


PUBLICATION_PATTERN = re.compile(
    r"\b(?:CN|US|EP|WO|JP|KR)\s*[-/]?\s*\d{5,}[A-Z0-9]*\b", re.IGNORECASE
)
NOVELTY_LABEL_PATTERN = re.compile(
    r"不具备新颖性|新颖性结论不确定|(?<!不)具备新颖性"
)

JUDGMENT_LABELS = {
    "FILE": "建议申请",
    "ADJUST_THEN_FILE": "调整后申请",
    "WATCH": "继续观察",
    "DO_NOT_FILE": "不建议申请",
    "INVENTIVE": "具备创造性",
    "NOT_INVENTIVE": "不具备创造性",
    "NEED_MORE_EVIDENCE": "需要更多证据",
    "UNCERTAIN": "结论不确定",
}


class ReportService:
    def __init__(
        self,
        database: Database,
        run_store: RunStore,
        agents: IdeaAgentService,
        *,
        citations: Any | None = None,
    ):
        self.database = database
        self.run_store = run_store
        self.agents = agents
        self.citations = citations

    async def generate(
        self,
        run_id: str,
        idea: IdeaParserOutput,
        novelty: NoveltyResult,
        inventive_routes: list[InventiveStepOutput],
        value: ValueAnalyzerOutput,
        audit_findings: list[dict[str, Any]],
        *,
        limitations: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        run = self.database.get_run(run_id)
        with self.database.connect() as connection:
            if connection.execute(
                "SELECT 1 FROM reports WHERE run_id = ?", (run_id,)
            ).fetchone():
                raise AgentExecutionError("report already exists for this run")
        facts = self._load_facts(run_id)
        verified_citations: tuple[VerifiedCitation, ...] = ()
        if self.citations is not None:
            verified_citations = tuple(await self.citations.for_run(run_id))
            if not verified_citations:
                raise AgentExecutionError(
                    "initial report RAG produced no verified Citations"
                )
            self._attach_citations(facts["deep_reviews"], verified_citations)
        novelty_label = {
            "NOVEL": "具备新颖性",
            "NOT_NOVEL": "不具备新颖性",
            "UNCERTAIN": "新颖性结论不确定",
        }[novelty.conclusion]
        payload = {
            "exact_novelty_label": novelty_label,
            "idea_title": idea.title,
            "idea_features": [
                {"feature_id": feature.feature_id, "feature_text": feature.feature_text}
                for feature in idea.features
            ],
            "evaluation_date": run["evaluation_date"],
            "known_publication_numbers": sorted(facts["publication_numbers"]),
            "novelty": {
                "conclusion": novelty.conclusion,
                "confidence": novelty.confidence,
                "closest_publication_number": novelty.closest_publication_number,
                "destroying_publication_number": novelty.destroying_publication_number,
                "missing_features": novelty.missing_features,
                "rationale": novelty.rationale,
            },
            "inventive_routes": [
                {
                    "route_id": route.route_id,
                    "d1_publication_number": route.d1_publication_number,
                    "status": route.status,
                    "rationale": route.overall_rationale,
                }
                for route in inventive_routes
            ],
            "value": {
                "recommendation": value.recommendation,
                "rationale": value.rationale,
            },
            "audit_issue_counts": self._audit_counts(audit_findings),
            "limitations": [
                item.get("message", str(item)) for item in (limitations or [])
            ] + novelty.limitations,
        }
        result = await self.agents.call_agent(
            run_id,
            "patent-report-composer",
            system_prompt=REPORT_COMPOSER_PROMPT,
            input_payload=payload,
            input_size=len(canonical_json(payload)),
        )
        narrative = result.output
        if not isinstance(narrative, ReportComposerOutput):
            raise AgentExecutionError("report composer returned wrong validated model")
        self._validate_narrative(narrative, novelty_label, facts["publication_numbers"])

        all_limitations = self._collect_limitations(
            novelty, inventive_routes, value, limitations or [], facts["provider_limitations"]
        )
        novelty_payload = novelty.model_dump(mode="json")
        if verified_citations:
            for matrix in novelty_payload["matrices"]:
                for mapping in matrix["mappings"]:
                    mapping["citations"] = [
                        item.to_dict()
                        for item in verified_citations
                        if item.feature_id == mapping["feature_id"]
                        and item.publication_number == matrix["publication_number"]
                    ]
        report = {
            "schema_version": "2.0" if verified_citations else "1.0",
            "run_id": run_id,
            "case_id": run["case_id"],
            "conclusion_overview": {
                "novelty_label": novelty_label,
                "novelty_code": novelty.conclusion,
                "novelty_confidence": novelty.confidence,
                "inventive_route_statuses": {
                    route.route_id: route.status for route in inventive_routes
                },
                "filing_recommendation": value.recommendation,
                "executive_summary": narrative.executive_summary,
            },
            "idea_features": [feature.model_dump(mode="json") for feature in idea.features],
            "evaluation": {
                "date": run["evaluation_date"],
                "date_basis": run["date_basis"],
                "analysis_scope": run["analysis_scope"],
            },
            "search_execution": facts["search_execution"],
            "provider_status": facts["provider_status"],
            "candidate_documents": facts["candidates"],
            "deep_review_documents": facts["deep_reviews"],
            "novelty": novelty_payload,
            "citations": [item.to_dict() for item in verified_citations],
            "inventiveness": [route.model_dump(mode="json") for route in inventive_routes],
            "value_assessment": value.model_dump(mode="json"),
            "simulated_office_action": narrative.simulated_office_action,
            "audit": {
                "counts": self._audit_counts(audit_findings),
                "findings": audit_findings,
            },
            "limitations": all_limitations,
            "provenance": {
                "model": run["model"],
                "skill_version": run["skill_version"],
                "workflow_version": run["workflow_version"],
                "config_snapshot": run["config_snapshot"],
            },
            "narrative": {
                "novelty_statement": narrative.novelty_statement,
                "inventive_step_statement": narrative.inventive_step_statement,
                "value_statement": narrative.value_statement,
                "action_recommendations": narrative.action_recommendations,
            },
        }
        markdown = self._markdown(report)
        manifest = self.run_store.write_reports(
            run["case_id"],
            run_id,
            report=report,
            markdown=markdown,
            manifest_metadata={
                "schema_version": report["schema_version"],
                "novelty_conclusion": novelty.conclusion,
                "workflow_version": run["workflow_version"],
                "citation_count": len(verified_citations),
                "citation_context_ids": sorted(
                    {item.context_id for item in verified_citations}
                ),
            },
        )
        self._persist_report(run, manifest)
        return report

    @staticmethod
    def _attach_citations(
        deep_reviews: list[dict[str, Any]],
        citations: tuple[VerifiedCitation, ...],
    ) -> None:
        for document in deep_reviews:
            for mapping in document["feature_mappings"]:
                mapping["citations"] = [
                    item.to_dict()
                    for item in citations
                    if item.feature_id == mapping["feature_id"]
                    and item.publication_number == document["publication_number"]
                ]

    def _load_facts(self, run_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            queries = [
                dict(row)
                for row in connection.execute(
                    """SELECT query_id,round_number,query_type,language,query_text,rationale
                    FROM search_queries WHERE run_id = ? ORDER BY round_number,query_id""",
                    (run_id,),
                ).fetchall()
            ]
            calls = [
                dict(row)
                for row in connection.execute(
                    """SELECT provider,operation,status,result_count,duration_ms,error_code,error_message
                    FROM tool_calls WHERE run_id = ? AND operation IN ('search','fetch')
                    ORDER BY created_at,call_id""",
                    (run_id,),
                ).fetchall()
            ]
            hit_rows = connection.execute(
                """SELECT provider,provider_rank,title,url,publication_number,application_number,
                          family_id,snippet,query_id
                FROM search_hits WHERE run_id = ? ORDER BY provider,provider_rank,hit_id""",
                (run_id,),
            ).fetchall()
            document_rows = connection.execute(
                """SELECT d.document_id,d.publication_number,d.title,d.assignee,d.priority_date,
                          d.publication_date,d.url,rd.relevance,rd.relevance_score,rd.found_by_json,
                          rd.query_ids_json
                FROM run_documents rd JOIN patent_documents d ON d.document_id = rd.document_id
                WHERE rd.run_id = ? AND rd.deep_reviewed = 1
                ORDER BY d.publication_number,d.document_id""",
                (run_id,),
            ).fetchall()
            deep_reviews = []
            publication_numbers = set()
            for document in document_rows:
                publication_numbers.add(document["publication_number"])
                mappings = []
                for mapping in connection.execute(
                    """SELECT fm.coverage_status,fm.confidence,fm.evidence_ids_json,fm.rationale,
                              f.metadata_json
                    FROM feature_mappings fm JOIN idea_features f ON f.feature_id = fm.feature_id
                    WHERE fm.run_id = ? AND fm.document_id = ? ORDER BY f.ordinal""",
                    (run_id, document["document_id"]),
                ).fetchall():
                    metadata = self._json(mapping["metadata_json"], {})
                    mappings.append(
                        {
                            "feature_id": metadata.get("external_feature_id"),
                            "status": mapping["coverage_status"],
                            "confidence": mapping["confidence"],
                            "evidence_ids": self._json(mapping["evidence_ids_json"], []),
                            "rationale": mapping["rationale"],
                        }
                    )
                deep_reviews.append(
                    {
                        "document_id": document["document_id"],
                        "publication_number": document["publication_number"],
                        "title": document["title"],
                        "assignee": document["assignee"],
                        "priority_date": document["priority_date"],
                        "publication_date": document["publication_date"],
                        "url": document["url"],
                        "relevance": document["relevance"],
                        "relevance_score": document["relevance_score"],
                        "found_by": self._json(document["found_by_json"], []),
                        "query_ids": self._json(document["query_ids_json"], []),
                        "feature_mappings": mappings,
                    }
                )

        candidate_map: dict[str, dict[str, Any]] = {}
        for row in hit_rows:
            key = row["publication_number"] or row["url"] or row["title"]
            if not key:
                continue
            item = candidate_map.setdefault(
                key,
                {
                    "publication_number": row["publication_number"],
                    "application_number": row["application_number"],
                    "family_id": row["family_id"],
                    "title": row["title"],
                    "url": row["url"],
                    "snippet": row["snippet"],
                    "sources": [],
                    "query_ids": [],
                },
            )
            if row["provider"] not in item["sources"]:
                item["sources"].append(row["provider"])
            if row["query_id"] and row["query_id"] not in item["query_ids"]:
                item["query_ids"].append(row["query_id"])
            if row["publication_number"]:
                publication_numbers.add(row["publication_number"])

        provider_status: dict[str, dict[str, Any]] = {}
        provider_limitations = []
        for call in calls:
            status = provider_status.setdefault(
                call["provider"], {"calls": 0, "successes": 0, "failures": 0, "result_count": 0}
            )
            status["calls"] += 1
            status["result_count"] += call["result_count"] or 0
            if call["status"] in {"SUCCESS", "EMPTY"}:
                status["successes"] += 1
            else:
                status["failures"] += 1
                provider_limitations.append(
                    {
                        "code": "PROVIDER_CALL_FAILURE",
                        "message": f"检索服务 {call['provider']} 的 {call['operation']} 调用返回 {call['status']}。",
                        "details": {"error_code": call["error_code"]},
                    }
                )
        return {
            "search_execution": {
                "queries": queries,
                "provider_call_count": len(calls),
                "raw_hit_count": len(hit_rows),
                "unique_candidate_count": len(candidate_map),
                "deep_review_count": len(deep_reviews),
            },
            "provider_status": provider_status,
            "provider_limitations": provider_limitations,
            "candidates": list(candidate_map.values()),
            "deep_reviews": deep_reviews,
            "publication_numbers": publication_numbers,
        }

    @staticmethod
    def _validate_narrative(
        narrative: ReportComposerOutput,
        novelty_label: str,
        known_publications: set[str],
    ) -> None:
        if not narrative.novelty_statement.strip().startswith(novelty_label):
            raise AgentExecutionError("report narrative contradicts the frozen novelty label")
        all_narrative = "\n".join(
            [
                narrative.executive_summary,
                narrative.novelty_statement,
                narrative.inventive_step_statement,
                narrative.value_statement,
                narrative.simulated_office_action,
                *narrative.action_recommendations,
            ]
        )
        detected_labels = {
            match.group(0) for match in NOVELTY_LABEL_PATTERN.finditer(all_narrative)
        }
        if detected_labels - {novelty_label}:
            raise AgentExecutionError("report narrative contains a conflicting novelty conclusion")
        known = {ReportService._identifier(value) for value in known_publications}
        text = canonical_json(narrative.model_dump(mode="json"))
        unknown = {
            match.group(0)
            for match in PUBLICATION_PATTERN.finditer(text)
            if ReportService._identifier(match.group(0)) not in known
        }
        if unknown:
            raise AgentExecutionError(
                f"report narrative introduced unknown publication numbers: {sorted(unknown)}"
            )

    @staticmethod
    def _identifier(value: str) -> str:
        return "".join(character for character in value.upper() if character.isalnum())

    @staticmethod
    def _json(value: str, default):
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return default

    @staticmethod
    def _audit_counts(findings: list[dict[str, Any]]) -> dict[str, int]:
        counts = {"critical": 0, "warning": 0, "info": 0}
        for finding in findings:
            severity = finding.get("severity")
            if severity in counts:
                counts[severity] += 1
        return counts

    @staticmethod
    def _collect_limitations(novelty, routes, value, supplied, provider):
        items = list(supplied) + list(provider)
        items.extend({"code": "NOVELTY_LIMITATION", "message": item} for item in novelty.limitations)
        for route in routes:
            items.extend(
                {"code": "INVENTIVE_LIMITATION", "message": item, "route_id": route.route_id}
                for item in route.limitations
            )
        items.extend({"code": "VALUE_LIMITATION", "message": item} for item in value.limitations)
        unique = {}
        for item in items:
            normalized = item if isinstance(item, dict) else {"code": "LIMITATION", "message": str(item)}
            key = (normalized.get("code"), normalized.get("message"), normalized.get("route_id"))
            unique[key] = normalized
        return list(unique.values())

    @staticmethod
    def _markdown(report: dict[str, Any]) -> str:
        overview = report["conclusion_overview"]
        evaluation = report["evaluation"]
        novelty = report["novelty"]
        lines = [
            f"# IDEA 专利评估报告：{overview['novelty_label']}",
            "",
            "## 1. 结论总览",
            "",
            overview["executive_summary"],
            "",
            f"- 新颖性：{overview['novelty_label']}（置信度 {overview['novelty_confidence']:.2f}）",
            f"- 申请建议：{ReportService._judgment_label(overview['filing_recommendation'])}",
            "",
            "## 2. IDEA 技术特征",
            "",
        ]
        lines.extend(
            f"- {item['feature_id']}：{item['feature_text']}" for item in report["idea_features"]
        )
        lines.extend([
            "", "## 3. 评估日期和依据", "",
            f"- 评估日：{evaluation['date']}", f"- 日期依据：{evaluation['date_basis']}",
            "", "## 4. 检索计划和实际执行", "",
            f"- 查询数：{len(report['search_execution']['queries'])}",
            f"- Provider 调用数：{report['search_execution']['provider_call_count']}",
            f"- 原始命中：{report['search_execution']['raw_hit_count']}",
            "", "## 5. Provider 状态与合并统计", "",
            f"```json\n{json.dumps(report['provider_status'], ensure_ascii=False, indent=2)}\n```",
            "", "## 6. 候选文献列表", "",
        ])
        lines.extend(
            f"- {ReportService._publication_link(item.get('publication_number'))}：{item.get('title') or '无标题'}"
            for item in report["candidate_documents"]
        )
        if not report["candidate_documents"]:
            lines.append("- 无持久化候选记录。")
        lines.extend(["", "## 7. 深度核验文献", ""])
        lines.extend(
            f"- {ReportService._publication_link(item['publication_number'])}：{item.get('title') or '无标题'}"
            for item in report["deep_review_documents"]
        )
        if not report["deep_review_documents"]:
            lines.append("- 无深度核验文献。")
        for document in report["deep_review_documents"]:
            for mapping in document["feature_mappings"]:
                citations = mapping.get("citations", [])
                if not citations:
                    continue
                lines.append(
                    f"- {mapping['feature_id']}：{mapping['status']}（置信度 {mapping['confidence']:.2f}）"
                )
                for citation in citations:
                    lines.append(
                        f"  - 依据：[{citation['alias']}] {citation['publication_number']}，{citation['section_label']}"
                    )
                    lines.append(f"    原文：{citation['excerpt']}")
        lines.extend([
            "", "## 8. 新颖性矩阵和结论", "", novelty["rationale"], "",
            f"- 最接近文献：{ReportService._publication_link(novelty['closest_publication_number'])}",
            f"- 未披露特征：{', '.join(novelty['missing_features']) or '无'}",
            "", "## 9. 创造性多 D1 路线", "",
        ])
        lines.extend(
            f"- {route['route_id']} / {ReportService._publication_link(route['d1_publication_number'])}：{ReportService._judgment_label(route['status'])}"
            for route in report["inventiveness"]
        )
        if not report["inventiveness"]:
            lines.append("- 新颖性已被破坏时，本节不适用。")
        value = report["value_assessment"]
        lines.extend([
            "", "## 10. 价值预评估", "",
            f"- 可取证性：{value['detectability']['rating']}/5 — {value['detectability']['rationale']}",
            f"- 规避难度：{value['workaround_difficulty']['rating']}/5 — {value['workaround_difficulty']['rationale']}",
            f"- 技术与市场价值：{value['technical_market_value']['rating']}/5 — {value['technical_market_value']['rationale']}",
            f"- 申请建议：{ReportService._judgment_label(value['recommendation'])}",
            "", value["rationale"],
            "", "## 11. 模拟审查意见", "", report["simulated_office_action"],
            "", "## 12. 证据审计", "",
            f"- 严重：{report['audit']['counts']['critical']}",
            f"- 警告：{report['audit']['counts']['warning']}",
            f"- 信息：{report['audit']['counts']['info']}",
            "", "## 13. 局限性", "",
        ])
        lines.extend(f"- {item.get('message', item)}" for item in report["limitations"])
        if not report["limitations"]:
            lines.append("- 无额外系统局限性记录。")
        provenance = report["provenance"]
        lines.extend([
            "", "## 14. 版本与溯源", "",
            f"- 模型：{provenance['model']}",
            f"- Skill：{provenance['skill_version']}",
            f"- Workflow：{provenance['workflow_version']}", "",
        ])
        return "\n".join(lines)

    @staticmethod
    def _judgment_label(value: str) -> str:
        return JUDGMENT_LABELS.get(value, value)

    @staticmethod
    def _publication_link(value: str | None) -> str:
        if not value:
            return "未标准化"
        identifier = ReportService._identifier(value)
        return f"[{identifier}](https://patents.google.com/patent/{identifier})"

    def _persist_report(self, run: dict[str, Any], manifest: dict[str, Any]) -> None:
        paths = self.run_store.paths(run["case_id"], run["run_id"])
        files = manifest["files"]
        with self.database.connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM reports WHERE run_id = ?", (run["run_id"],)
            ).fetchone()
            if exists:
                raise AgentExecutionError("report already exists for this run")
            connection.execute(
                "INSERT INTO reports VALUES(?,?,?,?,?,?,?)",
                (
                    run["run_id"], str(paths.report_json), files["report.json"]["sha256"],
                    str(paths.report_md), files["report.md"]["sha256"], str(paths.manifest), now_ms(),
                ),
            )
