from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from idea.agent_schemas import IdeaParserOutput, NoveltyResult, ValueAnalyzerOutput
from idea.agents import AgentExecutionError, IdeaAgentService
from idea.database import Database
from idea.model_client import AgentCallResult
from idea.value_analysis import ValueAnalysisService


class ValueModel:
    def __init__(self, *, unknown_basis=False, placeholder=False, empty_basis=False):
        self.settings = SimpleNamespace(provider="stub")
        self.payload = None
        self.unknown_basis = unknown_basis
        self.placeholder = placeholder
        self.empty_basis = empty_basis

    async def complete(self, agent_name, **kwargs):
        self.payload = kwargs["input_payload"]
        basis = "PATENT:invented" if self.unknown_basis else "IDEA:F1"
        output = ValueAnalyzerOutput.model_validate({
            "detectability": {"rating": 4, "rationale": "可通过运行行为观察", "evidence_basis": [] if self.empty_basis else [basis]},
            "workaround_difficulty": {"rating": 3, "rationale": "存在替代方案", "evidence_basis": ["IDEA:F2"]},
            "technical_market_value": {"rating": 2, "rationale": "仅作初步判断", "evidence_basis": ["NOVELTY:CONCLUSION"]},
            "alternative_paths": ["[待定]" if self.placeholder else "change the heat metric", "separate admission and eviction"],
            "recommendation": "ADJUST_THEN_FILE",
            "rationale": "preserve measurable implementation details",
            "limitations": ["no independent market dataset"],
        })
        return AgentCallResult(agent_name, "stub", output, 1, 5, {}, "value-1")


def idea():
    return IdeaParserOutput.model_validate({
        "title": "cache control", "technical_domains": ["storage"],
        "application_scenario": "accelerator", "technical_problem": "reduce misses",
        "features": [
            {"feature_id": "F1", "feature_text": "compute heat", "source_type": "normalized"},
            {"feature_id": "F2", "feature_text": "evict by heat", "source_type": "normalized"},
        ],
        "claimed_effects": ["less recomputation"], "subject_types": ["method"],
        "scope_breadth": "narrow",
    })


def novelty():
    return NoveltyResult.model_validate({
        "conclusion": "NOVEL", "confidence": 0.8,
        "matrices": [{
            "publication_number": "US1A1",
            "mappings": [
                {"feature_id": "F1", "status": "DISCLOSED", "evidence_ids": ["E1"], "rationale": "claim", "confidence": 0.9},
                {"feature_id": "F2", "status": "NOT_DISCLOSED", "evidence_ids": [], "rationale": "missing", "confidence": 0.9},
            ],
            "destroys_novelty": False,
        }],
        "destroying_publication_number": None, "closest_publication_number": "US1A1",
        "missing_features": ["F2"], "rationale": "具备新颖性",
    })


class ValueAnalysisServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "idea.db")
        self.db.initialize()
        case = self.db.create_case("Value")
        run = self.db.create_run(
            case_id=case["case_id"], input_text="idea", evaluation_date="2026-07-16",
            date_basis="default", analysis_scope="full", model="stub", skill_version="1",
            workflow_version="1", config_snapshot={},
        )
        self.run_id = run["run_id"]

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_valid_value_analysis_uses_only_frozen_summaries_and_persists(self) -> None:
        model = ValueModel()
        service = ValueAnalysisService(self.db, IdeaAgentService(self.db, model))
        output = asyncio.run(service.analyze(self.run_id, idea(), novelty(), []))
        self.assertEqual(output.recommendation, "ADJUST_THEN_FILE")
        self.assertNotIn("evidence", model.payload)
        self.assertNotIn("claims_text", json.dumps(model.payload))
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT result_json FROM value_results WHERE run_id = ?", (self.run_id,)
            ).fetchone()
        self.assertEqual(json.loads(row["result_json"])["recommendation"], "ADJUST_THEN_FILE")
        self.assertEqual(output.detectability.rating, 4)

    def test_value_score_must_be_between_one_and_five(self) -> None:
        value = {
            "rating": 0,
            "rationale": "无效分数",
            "evidence_basis": ["IDEA:F1"],
        }
        with self.assertRaises(ValueError):
            ValueAnalyzerOutput.model_validate(
                {
                    "detectability": value,
                    "workaround_difficulty": {**value, "rating": 3},
                    "technical_market_value": {**value, "rating": 6},
                    "alternative_paths": ["路径一", "路径二"],
                    "recommendation": "WATCH",
                    "rationale": "测试",
                }
            )

    def test_unknown_basis_id_is_rejected(self) -> None:
        model = ValueModel(unknown_basis=True)
        service = ValueAnalysisService(self.db, IdeaAgentService(self.db, model))
        with self.assertRaisesRegex(AgentExecutionError, "unknown basis"):
            asyncio.run(service.analyze(self.run_id, idea(), novelty(), []))

    def test_placeholder_alternative_is_rejected(self) -> None:
        model = ValueModel(placeholder=True)
        service = ValueAnalysisService(self.db, IdeaAgentService(self.db, model))
        with self.assertRaisesRegex(AgentExecutionError, "placeholder"):
            asyncio.run(service.analyze(self.run_id, idea(), novelty(), []))

    def test_every_dimension_requires_a_real_basis(self) -> None:
        model = ValueModel(empty_basis=True)
        service = ValueAnalysisService(self.db, IdeaAgentService(self.db, model))
        with self.assertRaisesRegex(AgentExecutionError, "every value dimension"):
            asyncio.run(service.analyze(self.run_id, idea(), novelty(), []))


if __name__ == "__main__":
    unittest.main()
