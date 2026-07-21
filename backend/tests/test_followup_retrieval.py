from __future__ import annotations

import asyncio
import unittest

from idea.config import load_config
from idea.followup import FollowupError, TurnStatus
from idea.followup_plan import FollowupPlan
from idea.followup_retrieval import MultiQueryFollowupRetriever
from idea.hybrid import HybridHit, HybridSearchResult, RetrievalMode
from backend.tests.test_followup_context import chunk, turn


class Hybrid:
    def __init__(self) -> None:
        self.settings = load_config().rag.hybrid
        self.requests = []

    async def search(self, request):
        self.requests.append(request)
        value = chunk(text="共同的权利要求证据")
        rank = len(self.requests)
        return HybridSearchResult(
            query_id=request.query_id,
            mode=RetrievalMode.LEXICAL_ONLY,
            retriever_version="hybrid-rrf-v1",
            hits=(HybridHit(
                chunk=value,
                lexical_rank=rank,
                vector_rank=None,
                rrf_score=1 / (60 + rank),
                section_weight=1.3,
                final_score=1.3 / (60 + rank),
                sources=("lexical",),
            ),),
            limitations=("LEXICAL_ONLY",),
        )


def plan(**updates):
    value = {
        "mode": "EVIDENCE_QA",
        "question_type": "CLAIM_OVERLAP",
        "selected_publication_numbers": ["CN123A"],
        "query_rewrites": ["热度 淘汰", "cache eviction"],
        "preferred_sections": ["claims"],
        "required_features": ["F1"],
        "requires_new_research": False,
        "requires_legal_review": False,
        "rationale": "检索两种表达。",
    }
    value.update(updates)
    return FollowupPlan.model_validate(value)


class FollowupRetrievalTests(unittest.TestCase):
    def test_rewrites_share_exact_scope_and_accumulate_rank_evidence(self) -> None:
        hybrid = Hybrid()
        result = asyncio.run(
            MultiQueryFollowupRetriever(hybrid).retrieve(
                turn=turn("current", status=TurnStatus.RUNNING, created_at=100),
                plan=plan(),
            )
        )

        self.assertEqual(len(hybrid.requests), 2)
        self.assertTrue(all(item.allowed_version_ids == ("cv-1",) for item in hybrid.requests))
        self.assertTrue(all(item.section_types == ("claims",) for item in hybrid.requests))
        self.assertNotEqual(hybrid.requests[0].query_id, hybrid.requests[1].query_id)
        self.assertEqual(len(result.hits), 1)
        self.assertAlmostEqual(result.hits[0].rrf_score, 1 / 61 + 1 / 62)
        self.assertEqual(result.hits[0].sources, ("Q1:lexical", "Q2:lexical"))
        self.assertEqual(result.mode, RetrievalMode.LEXICAL_ONLY)
        self.assertEqual(result.limitations, ("LEXICAL_ONLY",))

    def test_plan_cannot_retrieve_a_publication_outside_turn_scope(self) -> None:
        with self.assertRaisesRegex(FollowupError, "escaped the Turn scope"):
            asyncio.run(
                MultiQueryFollowupRetriever(Hybrid()).retrieve(
                    turn=turn("current", status=TurnStatus.RUNNING, created_at=100),
                    plan=plan(selected_publication_numbers=["US999B2"]),
                )
            )


if __name__ == "__main__":
    unittest.main()
