from __future__ import annotations

import hashlib
import asyncio
import unittest

from idea.agent_schemas import IdeaFeature
from idea.chunks import PatentChunk
from idea.context import ContextAssembler
from idea.followup import (
    FollowupError,
    FollowupMode,
    FollowupScope,
    FollowupScopeDocument,
    FollowupTurn,
    TurnStatus,
    question_hash,
)
from idea.followup_context import FollowupContextBuilder
from idea.hybrid import HybridHit, HybridSearchResult, RetrievalMode
from idea.postgres_followup import PostgreSQLFollowupRepository


def scope() -> FollowupScope:
    return FollowupScope.freeze((
        FollowupScopeDocument("doc-1", "cv-1", "CN123A", "a" * 64),
    ))


def turn(
    turn_id: str,
    *,
    status: TurnStatus,
    created_at: int,
    answer: dict | None = None,
    thread_id: str = "thread-1",
) -> FollowupTurn:
    question = f"问题 {turn_id}"
    return FollowupTurn(
        turn_id=turn_id,
        thread_id=thread_id,
        parent_turn_id=None,
        status=status,
        mode=FollowupMode.EVIDENCE_QA,
        question_text=question,
        question_hash=question_hash(question),
        scope=scope(),
        plan={"question_type": "CLAIM_OVERLAP"} if status != TurnStatus.QUEUED else None,
        answer=answer,
        model="deepseek-v4-flash",
        prompt_version="followup-v1",
        retriever_version="hybrid-rrf-v1",
        limitations=(),
        error_code=None,
        error_message=None,
        created_at=created_at,
        started_at=created_at if status != TurnStatus.QUEUED else None,
        completed_at=(
            created_at + 1
            if status in {TurnStatus.COMPLETED, TurnStatus.COMPLETED_WITH_LIMITATIONS}
            else None
        ),
    )


def chunk(*, version_id: str = "cv-1", publication: str = "CN123A", text: str = "权利要求技术证据") -> PatentChunk:
    return PatentChunk(
        chunk_id="chunk-" + hashlib.sha256(text.encode()).hexdigest()[:10],
        version_id=version_id,
        publication_number=publication,
        section_type="claims",
        section_label="claim-1",
        claim_number=1,
        claim_kind="independent",
        parent_claim_numbers=(),
        start_offset=0,
        end_offset=len(text),
        text=text,
        text_hash=hashlib.sha256(text.encode()).hexdigest(),
        token_count=4,
        chunker_version="v1",
    )


def retrieval(value: PatentChunk) -> HybridSearchResult:
    return HybridSearchResult(
        query_id="followup-query-1",
        mode=RetrievalMode.LEXICAL_ONLY,
        retriever_version="hybrid-rrf-v1",
        hits=(HybridHit(value, 1, None, 0.1, 1.3, 0.13, ("lexical",)),),
        limitations=("LEXICAL_ONLY",),
    )


class FollowupContextBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.current = turn("current", status=TurnStatus.RUNNING, created_at=100)
        self.feature = IdeaFeature(
            feature_id="F1", feature_text="根据热度淘汰缓存块", source_type="normalized"
        )

    def build(self, **updates):
        values = {
            "run_id": "run-1",
            "turn": self.current,
            "features": (self.feature,),
            "recent_turns": (),
            "retrieval": retrieval(chunk()),
            "system_prompt": "只根据本轮 Citation 证据回答。",
            "source_report_summary": "首次报告认为存在部分技术重合。",
            "source_report_limitations": ("仅评估公开文献",),
            "input_budget": 10000,
            "reserved_output_tokens": 1000,
        }
        values.update(updates)
        return FollowupContextBuilder(ContextAssembler()).build(**values)

    def test_build_is_deterministic_and_history_is_non_evidence(self) -> None:
        history = tuple(
            turn(
                f"old-{index}",
                status=TurnStatus.COMPLETED,
                created_at=index,
                answer={"direct_answer": f"历史回答 {index}", "citation_aliases": ["C1"]},
            )
            for index in range(1, 7)
        )
        first = self.build(recent_turns=history)
        second = self.build(recent_turns=tuple(reversed(history)))

        self.assertEqual(first.context.context_hash, second.context.context_hash)
        self.assertEqual(first.history_turn_ids, tuple(f"old-{index}" for index in range(2, 7)))
        self.assertEqual(first.selected_chunk_ids, (chunk().chunk_id,))
        self.assertEqual(first.context.allowed_version_ids, ("cv-1",))
        self.assertEqual(first.context.selected_chunks[0]["alias"], "C1")
        kinds = [item["kind"] for item in first.context.selected_notes]
        self.assertEqual(kinds[:3], ["FROZEN_SCOPE", "IDEA_FEATURES", "SOURCE_REPORT"])
        self.assertEqual(kinds.count("RECENT_TURN"), 5)
        self.assertIn("HISTORY_WINDOW_LIMIT", first.context.limitations)
        self.assertIn("LEXICAL_ONLY", first.context.limitations)
        self.assertIn("not patent evidence", first.context.messages[1].content)

    def test_evidence_scope_and_history_fail_closed(self) -> None:
        with self.assertRaisesRegex(FollowupError, "escaped the frozen Turn scope"):
            self.build(retrieval=retrieval(chunk(version_id="cv-9")))

        failed = turn("failed", status=TurnStatus.FAILED, created_at=1)
        with self.assertRaisesRegex(FollowupError, "completed answered Turns"):
            self.build(recent_turns=(failed,))

        foreign = turn(
            "foreign", status=TurnStatus.COMPLETED, created_at=1,
            answer={"direct_answer": "x"}, thread_id="thread-2",
        )
        with self.assertRaisesRegex(FollowupError, "escaped the current Thread"):
            self.build(recent_turns=(foreign,))

    def test_evidence_has_budget_priority_over_long_history(self) -> None:
        long_history = turn(
            "old", status=TurnStatus.COMPLETED, created_at=1,
            answer={"direct_answer": "历史" * 500},
        )
        prepared = FollowupContextBuilder(ContextAssembler(token_counter=len)).build(
            run_id="run-1",
            turn=self.current,
            features=(self.feature,),
            recent_turns=(long_history,),
            retrieval=retrieval(chunk()),
            system_prompt="规则",
            input_budget=300,
            reserved_output_tokens=10,
        )

        self.assertEqual(prepared.selected_chunk_ids, (chunk().chunk_id,))
        self.assertTrue(prepared.context.excluded_notes)
        self.assertIn("CONTEXT_NOTE_BUDGET_EXCLUSIONS", prepared.context.limitations)

    def test_citation_aliases_remain_contiguous_when_earlier_chunk_is_excluded(self) -> None:
        large = chunk(text="大" * 500)
        small = chunk(text="短证据")
        result = ContextAssembler(token_counter=len).assemble(
            purpose="FOLLOWUP",
            run_id="run-1",
            turn_id="turn-1",
            corpus_snapshot_hash="b" * 64,
            chunks=(large, small),
            system_prompt="规则",
            question="问题",
            prompt_version="v1",
            retriever_version="v1",
            input_budget=100,
            reserved_output_tokens=10,
        )

        self.assertEqual(result.selected_chunks[0]["alias"], "C1")
        self.assertEqual(result.selected_chunks[0]["rank"], 2)

    def test_persistence_selection_must_be_a_retrieval_subset(self) -> None:
        repository = PostgreSQLFollowupRepository("postgresql://unused")
        with self.assertRaisesRegex(FollowupError, "escaped retrieval results"):
            asyncio.run(
                repository.record_retrieval(
                    "turn-1",
                    retrieval(chunk()),
                    selected_chunk_ids=("unknown-chunk",),
                )
            )


if __name__ == "__main__":
    unittest.main()
