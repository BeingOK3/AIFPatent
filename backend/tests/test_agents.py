from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from idea.agent_schemas import IdeaParserOutput, QueryPlannerOutput
from idea.agents import AgentExecutionError, IdeaAgentService
from idea.database import Database
from idea.model_client import AgentCallResult


class StubModel:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.settings = SimpleNamespace(provider="stub")

    async def complete(self, agent_name, **kwargs):
        output = self.outputs.pop(0)
        return AgentCallResult(
            agent_name=agent_name,
            model="stub-model",
            output=output,
            attempts=1,
            duration_ms=5,
            usage={"total_tokens": 10},
            response_id="stub-response",
        )


def parser_output(text="缓存调度", start=0, end=4):
    return IdeaParserOutput.model_validate({
        "title": "缓存调度方法",
        "technical_domains": ["计算机存储"],
        "application_scenario": "数据中心",
        "technical_problem": "降低缓存未命中",
        "features": [{
            "feature_id": "F1",
            "feature_text": "根据局部性调度缓存",
            "source_type": "explicit",
            "source_span": {"start": start, "end": end, "text": text},
            "required": True,
        }],
        "claimed_effects": ["降低未命中"],
        "subject_types": ["method"],
        "scope_breadth": "narrow",
    })


def planner_output(query_text="(cache locality OR cache affinity) AND scheduling"):
    return QueryPlannerOutput.model_validate({
        "term_groups": [
            {"concept": "cache", "zh_terms": ["缓存"], "en_terms": ["cache"]},
            {"concept": "scheduling", "zh_terms": ["调度"], "en_terms": ["scheduling"]},
        ],
        "ipc_cpc_candidates": ["G06F12/00"],
        "queries": [
            {
                "query_id": "Q1",
                "round_number": 1,
                "query_type": "technical_means",
                "language": "en",
                "query_text": query_text,
                "rationale": "same technical means",
            },
            {
                "query_id": "Q2",
                "round_number": 1,
                "query_type": "problem_effect",
                "language": "zh",
                "query_text": "(缓存未命中 OR 数据局部性) AND 调度",
                "rationale": "same problem",
            },
        ],
    })


class IdeaAgentServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "idea.db")
        self.db.initialize()
        case = self.db.create_case("Agent test")
        self.run = self.db.create_run(
            case_id=case["case_id"], input_text="缓存调度方法", evaluation_date="2026-07-16",
            date_basis="default", analysis_scope="full", model="stub", skill_version="1",
            workflow_version="1", config_snapshot={},
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_valid_parser_persists_features_and_redacted_call_audit(self) -> None:
        service = IdeaAgentService(self.db, StubModel([parser_output()]))
        result = asyncio.run(service.parse_idea(self.run["run_id"], "缓存调度方法"))
        self.assertEqual(result.features[0].feature_id, "F1")
        with self.db.connect() as connection:
            feature = dict(connection.execute("SELECT * FROM idea_features").fetchone())
            call = dict(connection.execute("SELECT * FROM tool_calls").fetchone())
        self.assertEqual(feature["source_start"], 0)
        self.assertNotIn("缓存调度方法", call["request_json"])
        self.assertEqual(call["status"], "SUCCESS")
        checkpoint = self.db.get_stage_result(self.run["run_id"], "PARSE_IDEA")
        self.assertEqual(checkpoint["value"]["title"], result.title)

    def test_mismatched_source_span_is_rejected_before_persistence(self) -> None:
        service = IdeaAgentService(self.db, StubModel([parser_output(text="不匹配")]))
        with self.assertRaisesRegex(AgentExecutionError, "does not match"):
            asyncio.run(service.parse_idea(self.run["run_id"], "缓存调度方法"))
        with self.db.connect() as connection:
            count = connection.execute("SELECT COUNT(*) FROM idea_features").fetchone()[0]
        self.assertEqual(count, 0)

    def test_exact_quote_repairs_incorrect_unicode_offsets_before_persistence(self) -> None:
        service = IdeaAgentService(
            self.db, StubModel([parser_output(text="缓存调度", start=2, end=6)])
        )
        result = asyncio.run(service.parse_idea(self.run["run_id"], "缓存调度方法"))
        span = result.features[0].source_span
        self.assertEqual((span.start, span.end, span.text), (0, 4, "缓存调度"))
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT source_start,source_end FROM idea_features"
            ).fetchone()
        self.assertEqual((row["source_start"], row["source_end"]), (0, 4))

    def test_repeated_quote_uses_occurrence_nearest_to_proposed_offset(self) -> None:
        output = parser_output(text="缓存", start=6, end=8)
        resolved = IdeaAgentService._resolve_source_spans("缓存调度与缓存隔离", output)
        span = resolved.features[0].source_span
        self.assertEqual((span.start, span.end), (5, 7))

    def test_valid_query_plan_persists_qualified_query_ids(self) -> None:
        idea = parser_output()
        service = IdeaAgentService(self.db, StubModel([planner_output()]))
        result = asyncio.run(service.plan_queries(self.run["run_id"], idea, per_query_limit=20))
        self.assertGreater(len(result.queries), 2)
        self.assertTrue(
            all(
                query.query_id == f"Q{index}"
                for index, query in enumerate(result.queries, 1)
            )
        )
        with self.db.connect() as connection:
            rows = connection.execute(
                "SELECT query_id FROM search_queries ORDER BY query_id"
            ).fetchall()
        self.assertEqual(
            {row["query_id"] for row in rows},
            {f"{self.run['run_id']}:{query.query_id}" for query in result.queries},
        )
        checkpoint = self.db.get_stage_result(self.run["run_id"], "PLAN_QUERIES")
        self.assertEqual(len(checkpoint["value"]["queries"]), len(result.queries))

    def test_query_placeholder_is_rejected(self) -> None:
        service = IdeaAgentService(
            self.db, StubModel([planner_output("[关键词] AND cache")])
        )
        with self.assertRaisesRegex(AgentExecutionError, "placeholder"):
            asyncio.run(
                service.plan_queries(self.run["run_id"], parser_output(), per_query_limit=20)
            )


if __name__ == "__main__":
    unittest.main()
