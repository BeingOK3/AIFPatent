from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from idea.agent_schemas import IdeaParserOutput, NoveltyResult, ReportComposerOutput, ValueAnalyzerOutput
from idea.agents import AgentExecutionError, IdeaAgentService
from idea.citations import VerifiedCitation
from idea.database import Database
from idea.model_client import AgentCallResult
from idea.reporting import ReportService
from idea.run_store import RunStore


class ReportModel:
    def __init__(self, *, label="具备新颖性", invented_publication=None):
        self.settings = SimpleNamespace(provider="stub")
        self.label = label
        self.invented_publication = invented_publication

    async def complete(self, agent_name, **kwargs):
        suffix = f"，参见 {self.invented_publication}" if self.invented_publication else ""
        output = ReportComposerOutput.model_validate({
            "executive_summary": "在本次检索范围内形成受控评估。" + suffix,
            "novelty_statement": self.label + "，理由见单篇矩阵。",
            "inventive_step_statement": "创造性路线证据仍需结合区别特征。",
            "value_statement": "价值判断属于初步评估。",
            "simulated_office_action": "审查员可能要求进一步限定可测量参数。",
            "action_recommendations": ["补充实施例", "保留区别特征"],
        })
        return AgentCallResult(agent_name, "stub", output, 1, 5, {}, "report-1")


def idea():
    return IdeaParserOutput.model_validate({
        "title": "cache", "technical_domains": ["storage"], "application_scenario": "GPU",
        "technical_problem": "reduce misses",
        "features": [{"feature_id": "F1", "feature_text": "token heat eviction", "source_type": "normalized"}],
        "claimed_effects": ["less recomputation"], "subject_types": ["method"], "scope_breadth": "narrow",
    })


def novelty():
    return NoveltyResult.model_validate({
        "conclusion": "NOVEL", "confidence": 0.81,
        "matrices": [{
            "publication_number": "US123456A1",
            "mappings": [{"feature_id": "F1", "status": "NOT_DISCLOSED", "evidence_ids": [], "rationale": "missing", "confidence": 0.9}],
            "destroys_novelty": False,
        }],
        "destroying_publication_number": None, "closest_publication_number": "US123456A1",
        "missing_features": ["F1"], "rationale": "在本次范围内具备新颖性。",
    })


def value():
    return ValueAnalyzerOutput.model_validate({
        "detectability": {"rating": 3, "rationale": "可观察", "evidence_basis": ["IDEA:F1"]},
        "workaround_difficulty": {"rating": 3, "rationale": "存在替代方案", "evidence_basis": ["IDEA:F1"]},
        "technical_market_value": {"rating": 3, "rationale": "初步判断", "evidence_basis": ["NOVELTY:CONCLUSION"]},
        "alternative_paths": ["metric change", "two-stage control"], "recommendation": "FILE",
        "rationale": "retain measurable feature", "limitations": ["no market dataset"],
    })


class ReportingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.db = Database(root / "idea.db")
        self.db.initialize()
        self.store = RunStore(root / "runs")
        case = self.db.create_case("Report")
        self.run = self.db.create_run(
            case_id=case["case_id"], input_text="idea", evaluation_date="2026-07-16",
            date_basis="user", analysis_scope="full", model="stub", skill_version="2",
            workflow_version="3", config_snapshot={"cache": "fifo"},
        )
        self.run_id = self.run["run_id"]
        self.store.initialize_run(case["case_id"], self.run_id)
        self.store.snapshot_input(
            case["case_id"], self.run_id, input_text="idea", metadata={"source": "test"}
        )
        with self.db.connect() as connection:
            connection.execute(
                """INSERT INTO patent_documents(
                    document_id,publication_number,title,publication_date,language,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?)""",
                ("doc-1", "US123456A1", "Prior cache", "2020-01-01", "en", 1, 1),
            )
            connection.execute(
                "INSERT INTO run_documents(run_id,document_id,screening_status,deep_reviewed) VALUES(?,?,'ANALYZED',1)",
                (self.run_id, "doc-1"),
            )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def service(self, model, citations=None):
        return ReportService(
            self.db, self.store, IdeaAgentService(self.db, model), citations=citations
        )

    def test_rag_report_emits_verified_citations_in_json_and_markdown(self) -> None:
        with self.db.connect() as connection:
            connection.execute(
                """INSERT INTO idea_features(
                    feature_id,run_id,ordinal,feature_text,source_type,metadata_json
                ) VALUES(?,?,?,?,?,?)""",
                (f"{self.run_id}:F1", self.run_id, 1, "token heat eviction",
                 "normalized", '{"external_feature_id":"F1"}'),
            )
            connection.execute(
                """INSERT INTO feature_mappings(
                    mapping_id,run_id,document_id,feature_id,coverage_status,
                    confidence,evidence_ids_json,rationale
                ) VALUES(?,?,?,?,?,?,?,?)""",
                ("map-1", self.run_id, "doc-1", f"{self.run_id}:F1",
                 "PARTIAL", 0.7, '["E1"]', "partial disclosure"),
            )
        citation = VerifiedCitation(
            context_id="CTX-fixture", feature_id="F1", alias="C1",
            chunk_id="chunk-1", version_id="cv-1",
            publication_number="US123456A1", section_type="claims",
            section_label="claim-1", claim_number=1, start_offset=0,
            end_offset=22, text_hash="a" * 64,
            excerpt="1. A cache controller.",
        )

        class CitationRepository:
            async def for_run(self, run_id):
                self.run_id = run_id
                return (citation,)

        report = asyncio.run(
            self.service(ReportModel(), CitationRepository()).generate(
                self.run_id, idea(), novelty(), [], value(), []
            )
        )

        self.assertEqual(report["schema_version"], "2.0")
        self.assertEqual(report["citations"][0]["chunk_id"], "chunk-1")
        mapping = report["deep_review_documents"][0]["feature_mappings"][0]
        self.assertEqual(mapping["citations"][0]["alias"], "C1")
        markdown = self.store.paths(self.run["case_id"], self.run_id).report_md.read_text()
        self.assertIn("依据：[C1] US123456A1，claim-1", markdown)
        self.assertIn("原文：1. A cache controller.", markdown)

    def test_authoritative_json_markdown_manifest_and_hashes_are_saved(self) -> None:
        report = asyncio.run(self.service(ReportModel()).generate(
            self.run_id, idea(), novelty(), [], value(),
            [{"severity": "info", "code": "AUDIT_COMPLETED", "message": "ok", "details": {}}],
        ))
        self.assertEqual(report["conclusion_overview"]["novelty_label"], "具备新颖性")
        paths = self.store.paths(self.run["case_id"], self.run_id)
        self.assertTrue(paths.report_json.is_file())
        markdown = paths.report_md.read_text(encoding="utf-8")
        for number in range(1, 15):
            self.assertIn(f"## {number}.", markdown)
        self.assertIn("[US123456A1](https://patents.google.com/patent/US123456A1)", markdown)
        self.assertIn("- 申请建议：建议申请", markdown)
        self.assertIn("- 可取证性：3/5", markdown)
        self.assertIn("- 严重：0", markdown)
        self.assertNotIn("- Critical：", markdown)
        self.store.verify(self.run["case_id"], self.run_id)
        with self.db.connect() as connection:
            row = connection.execute("SELECT * FROM reports WHERE run_id = ?", (self.run_id,)).fetchone()
        manifest = json.loads(paths.manifest.read_text(encoding="utf-8"))
        self.assertEqual(row["report_json_hash"], manifest["files"]["report.json"]["sha256"])

    def test_contradictory_novelty_narrative_is_rejected_before_writes(self) -> None:
        with self.assertRaisesRegex(AgentExecutionError, "contradicts"):
            asyncio.run(self.service(ReportModel(label="不具备新颖性")).generate(
                self.run_id, idea(), novelty(), [], value(), []
            ))
        self.assertFalse(self.store.paths(self.run["case_id"], self.run_id).report_json.exists())

    def test_unknown_publication_in_narrative_is_rejected(self) -> None:
        with self.assertRaisesRegex(AgentExecutionError, "unknown publication"):
            asyncio.run(self.service(ReportModel(invented_publication="US999999A1")).generate(
                self.run_id, idea(), novelty(), [], value(), []
            ))

    def test_label_prefix_cannot_hide_a_later_conflicting_conclusion(self) -> None:
        model = ReportModel(label="具备新颖性，但不具备新颖性")
        with self.assertRaisesRegex(AgentExecutionError, "conflicting novelty"):
            asyncio.run(self.service(model).generate(
                self.run_id, idea(), novelty(), [], value(), []
            ))

    def test_repeated_expected_novelty_label_is_not_a_conflict(self) -> None:
        for label in ("具备新颖性", "不具备新颖性", "新颖性结论不确定"):
            with self.subTest(label=label):
                narrative = ReportComposerOutput.model_validate({
                    "executive_summary": f"总体判断为{label}。",
                    "novelty_statement": f"{label}，并且证据支持该结论。",
                    "inventive_step_statement": "创造性另行判断。",
                    "value_statement": f"价值建议以{label}为前提。",
                    "simulated_office_action": "审查员将核验证据。",
                    "action_recommendations": [f"围绕{label}准备答复。"],
                })
                ReportService._validate_narrative(narrative, label, set())

    def test_negative_label_does_not_hide_a_positive_conclusion(self) -> None:
        narrative = ReportComposerOutput.model_validate({
            "executive_summary": "总体判断不具备新颖性。",
            "novelty_statement": "不具备新颖性，存在单篇破坏性文献。",
            "inventive_step_statement": "但后文错误宣称具备新颖性。",
            "value_statement": "不建议申请。",
            "simulated_office_action": "审查员将核验证据。",
            "action_recommendations": ["调整方案。"],
        })
        with self.assertRaisesRegex(AgentExecutionError, "conflicting novelty"):
            ReportService._validate_narrative(narrative, "不具备新颖性", set())


if __name__ == "__main__":
    unittest.main()
