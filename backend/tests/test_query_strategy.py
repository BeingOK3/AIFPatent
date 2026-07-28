from __future__ import annotations

import unittest

from idea.agent_schemas import QueryPlannerOutput
from idea.query_strategy import (
    compile_provider_query,
    expand_recall_plan,
    split_for_recall,
)


def overconstrained_plan() -> QueryPlannerOutput:
    return QueryPlannerOutput.model_validate(
        {
            "term_groups": [
                {
                    "concept": "缩进层级",
                    "zh_terms": ["缩进层级", "缩进深度"],
                    "en_terms": ["indentation level", "indent depth"],
                },
                {
                    "concept": "表格识别",
                    "zh_terms": ["表格识别", "表格结构"],
                    "en_terms": ["table recognition", "table structure"],
                },
            ],
            "ipc_cpc_candidates": ["G06V30/10", "G06V30/19"],
            "queries": [
                {
                    "query_id": "Q1",
                    "round_number": 1,
                    "query_type": "technical_means",
                    "language": "mixed",
                    "query_text": (
                        '("缩进" OR indentation) AND ("表格" OR table) '
                        'AND ("编码器" OR encoder) AND ("并行通道" OR "parallel channel")'
                    ),
                    "rationale": "核心技术",
                },
                {
                    "query_id": "Q2",
                    "round_number": 1,
                    "query_type": "problem_effect",
                    "language": "mixed",
                    "query_text": '("层级恢复" OR "hierarchy recovery") AND OCR',
                    "rationale": "问题效果",
                },
            ],
        }
    )


class QueryStrategyTests(unittest.TestCase):
    def test_overconstrained_query_is_split_into_bounded_recall_queries(self) -> None:
        variants = split_for_recall(
            "(alpha OR beta) AND (gamma OR delta) AND (epsilon OR zeta)"
        )
        self.assertGreaterEqual(len(variants), 3)
        self.assertTrue(all(item.upper().count(" AND ") <= 1 for item in variants))

    def test_recall_plan_adds_single_concept_and_classification_fallbacks(self) -> None:
        expanded = expand_recall_plan(overconstrained_plan())
        self.assertGreater(len(expanded.queries), 2)
        self.assertLessEqual(len(expanded.queries), 24)
        self.assertTrue(
            all(
                query.query_id == f"Q{index}"
                for index, query in enumerate(expanded.queries, 1)
            )
        )
        self.assertTrue(
            any(query.query_type == "classification" for query in expanded.queries)
        )
        self.assertTrue(
            any("缩进层级" in query.query_text for query in expanded.queries)
        )
        self.assertTrue(
            all("IPC:" not in query.query_text for query in expanded.queries)
        )

    def test_provider_compiler_removes_field_syntax_and_boolean_for_exa(self) -> None:
        query = 'IPC:(G06V30/10 OR G06V30/19) AND ("table recognition" OR OCR)'
        serpapi = compile_provider_query(query, "serpapi_google_patents")
        exa = compile_provider_query(query, "exa_mcp")
        self.assertNotIn("IPC:", serpapi)
        self.assertNotIn(" AND ", exa.upper())
        self.assertNotIn(" OR ", exa.upper())


if __name__ == "__main__":
    unittest.main()
