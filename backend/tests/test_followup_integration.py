from __future__ import annotations

import asyncio
import os
import unittest
import uuid

from idea.chunks import PatentChunk
from idea.followup import (
    FollowupCitationDraft,
    FollowupError,
    FollowupMode,
    ThreadStatus,
    TurnStatus,
)
from idea.hybrid import HybridHit, HybridSearchResult, RetrievalMode
from idea.postgres_followup import PostgreSQLFollowupRepository
from idea.postgres_followup_data import PostgreSQLFollowupDataSource


@unittest.skipUnless(
    os.environ.get("AIFPATENT_RUN_FOLLOWUP_INTEGRATION") == "1",
    "set AIFPATENT_RUN_FOLLOWUP_INTEGRATION=1 to test real follow-up persistence",
)
class FollowupIntegrationTests(unittest.TestCase):
    def test_thread_scope_turn_transitions_and_database_immutability(self) -> None:
        asyncio.run(self._run_scenario())

    async def _run_scenario(self) -> None:
        import psycopg

        dsn = os.environ["AIFPATENT_POSTGRES_DSN"]
        connection = await psycopg.AsyncConnection.connect(dsn)
        repository = PostgreSQLFollowupRepository(dsn)
        thread_id = None
        case_id = "followup-integration-case-" + uuid.uuid4().hex
        run_id = "followup-integration-run-" + uuid.uuid4().hex
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT rdv.document_id, rdv.version_id
                    FROM run_document_versions rdv
                    JOIN patent_document_versions pv ON pv.version_id = rdv.version_id
                    WHERE rdv.deep_reviewed = TRUE
                      AND rdv.corpus_availability = 'READY'
                      AND pv.state = 'READY'
                    ORDER BY rdv.version_id
                    LIMIT 1
                    """
                )
                row = await cursor.fetchone()
                self.assertIsNotNone(row, "integration database needs one READY Corpus Version")
                document_id, version_id = str(row[0]), str(row[1])
                timestamp = 1_785_000_000_000
                await cursor.execute(
                    """
                    INSERT INTO idea_cases(case_id, title, created_at, updated_at)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (case_id, f"Follow-up integration {case_id}", timestamp, timestamp),
                )
                await cursor.execute(
                    """
                    INSERT INTO idea_runs(
                        run_id, case_id, status, evaluation_date, date_basis,
                        analysis_scope, model, skill_version, workflow_version,
                        config_snapshot, created_at, started_at, completed_at
                    ) VALUES (
                        %s, %s, 'COMPLETED', '2026-07-22', 'publication',
                        'integration', 'fixture', 'integration/1', 'integration/1',
                        '{}'::jsonb, %s, %s, %s
                    )
                    """,
                    (run_id, case_id, timestamp, timestamp, timestamp),
                )
                await cursor.execute(
                    """
                    INSERT INTO run_document_versions(
                        run_id, document_id, version_id, deep_reviewed,
                        corpus_availability, linked_at
                    ) VALUES (%s, %s, %s, TRUE, 'READY', %s)
                    """,
                    (run_id, document_id, version_id, timestamp),
                )
                await cursor.execute(
                    """
                    INSERT INTO idea_features(
                        feature_id, run_id, ordinal, feature_text, source_type,
                        metadata_json
                    ) VALUES (%s, %s, 1, %s, 'normalized', %s::jsonb)
                    """,
                    (
                        f"{run_id}:F1",
                        run_id,
                        "根据热度淘汰缓存块",
                        '{"external_feature_id":"F1","required":true}',
                    ),
                )
            await connection.commit()

            thread = await repository.create_thread(
                run_id=run_id,
                title="Integration follow-up",
                default_mode=FollowupMode.EVIDENCE_QA,
            )
            thread_id = thread.thread_id
            self.assertEqual(thread.status, ThreadStatus.ACTIVE)
            self.assertTrue(thread.scope.version_ids)
            self.assertEqual((await repository.get_thread(thread_id)), thread)

            turn = await repository.create_turn(
                thread_id=thread_id,
                question="这篇专利披露了哪些缓存淘汰特征？",
                model="fixture-chat-model",
                prompt_version="followup-prompt-v1",
                retriever_version="hybrid-rrf-v1",
                publication_numbers=(thread.scope.publication_numbers[0],),
            )
            self.assertEqual(turn.status, TurnStatus.QUEUED)
            self.assertEqual(len(turn.scope.documents), 1)
            running = await repository.start_turn(turn.turn_id)
            self.assertEqual(running.status, TurnStatus.RUNNING)
            planned = await repository.save_plan(
                turn.turn_id,
                {"lexical_queries": ["缓存 淘汰"], "semantic_query": "缓存淘汰特征"},
            )
            self.assertIsNotNone(planned.plan)
            source = await PostgreSQLFollowupDataSource(dsn).load(
                run_id=run_id, turn=planned
            )
            self.assertEqual(source.features[0].feature_id, "F1")
            self.assertEqual(source.recent_turns, ())
            self.assertIn('"run_status":"COMPLETED"', source.report_summary)
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT chunk_id, version_id, publication_number, section_type,
                           section_label, claim_number, claim_kind, parent_claims_json,
                           start_offset, end_offset, text, text_hash, token_count,
                           chunker_version
                    FROM patent_chunks WHERE version_id = %s
                    ORDER BY chunk_id LIMIT 1
                    """,
                    (planned.scope.version_ids[0],),
                )
                chunk_row = await cursor.fetchone()
            self.assertIsNotNone(chunk_row)
            chunk = PatentChunk(
                chunk_id=str(chunk_row[0]), version_id=str(chunk_row[1]),
                publication_number=str(chunk_row[2]), section_type=str(chunk_row[3]),
                section_label=str(chunk_row[4]),
                claim_number=(int(chunk_row[5]) if chunk_row[5] is not None else None),
                claim_kind=(str(chunk_row[6]) if chunk_row[6] is not None else None),
                parent_claim_numbers=tuple(chunk_row[7] or []),
                start_offset=int(chunk_row[8] or 0), end_offset=int(chunk_row[9] or 0),
                text=str(chunk_row[10]), text_hash=str(chunk_row[11]),
                token_count=int(chunk_row[12]), chunker_version=str(chunk_row[13]),
            )
            retrieval = HybridSearchResult(
                query_id="integration-followup-query",
                mode=RetrievalMode.LEXICAL_ONLY,
                retriever_version="hybrid-rrf-v1",
                hits=(
                    HybridHit(
                        chunk=chunk, lexical_rank=1, vector_rank=None,
                        rrf_score=1 / 61, section_weight=1.0,
                        final_score=1 / 61, sources=("lexical",),
                    ),
                ),
                limitations=("LEXICAL_ONLY",),
            )
            self.assertEqual(
                await repository.record_retrieval(
                    turn.turn_id,
                    retrieval,
                    selected_chunk_ids=(chunk.chunk_id,),
                ),
                1,
            )
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT selected_for_context
                    FROM followup_retrieval_hits
                    WHERE turn_id = %s AND chunk_id = %s
                    """,
                    (turn.turn_id, chunk.chunk_id),
                )
                selected_row = await cursor.fetchone()
            self.assertEqual(selected_row, (True,))
            quote_start = chunk.start_offset
            quote_end = quote_start + len(chunk.text)
            citations = await repository.record_citations(
                turn.turn_id,
                (
                    FollowupCitationDraft(
                        chunk_id=chunk.chunk_id, quote_text=chunk.text,
                        start_offset=quote_start, end_offset=quote_end,
                        answer_path="items[0].analysis",
                    ),
                ),
            )
            self.assertEqual(len(citations), 1)
            with self.assertRaisesRegex(FollowupError, "does not match"):
                await repository.record_citations(
                    turn.turn_id,
                    (
                        FollowupCitationDraft(
                            chunk_id=chunk.chunk_id, quote_text="tampered quote",
                            start_offset=quote_start, end_offset=quote_end,
                            answer_path="items[1].analysis",
                        ),
                    ),
                )
            completed = await repository.complete_turn(
                turn.turn_id,
                answer={
                    "status": "ANSWERED",
                    "items": [{"analysis": "有原文依据", "citations": [citations[0].citation_id]}],
                },
                limitations=("LEXICAL_ONLY",),
            )
            self.assertEqual(completed.status, TurnStatus.COMPLETED_WITH_LIMITATIONS)
            with self.assertRaisesRegex(FollowupError, "invalid or already terminal"):
                await repository.cancel_turn(turn.turn_id)

            child = await repository.create_turn(
                thread_id=thread_id,
                parent_turn_id=turn.turn_id,
                question="请进一步解释第一项。",
                model="fixture-chat-model",
                prompt_version="followup-prompt-v1",
                retriever_version="hybrid-rrf-v1",
            )
            await repository.start_turn(child.turn_id)
            child_source = await PostgreSQLFollowupDataSource(dsn).load(
                run_id=run_id, turn=await repository.get_turn(child.turn_id)
            )
            self.assertEqual([item.turn_id for item in child_source.recent_turns], [turn.turn_id])
            failed = await repository.fail_turn(
                child.turn_id,
                error_code="FIXTURE_FAILURE",
                error_message="controlled integration failure\nwithout secrets",
            )
            self.assertEqual(failed.status, TurnStatus.FAILED)
            self.assertNotIn("\n", failed.error_message)

            async with connection.cursor() as cursor:
                with self.assertRaises(psycopg.errors.RaiseException):
                    await cursor.execute(
                        "UPDATE followup_turns SET question_text = 'tampered' WHERE turn_id = %s",
                        (turn.turn_id,),
                    )
            await connection.rollback()

            archived = await repository.archive_thread(thread_id)
            self.assertEqual(archived.status, ThreadStatus.ARCHIVED)
            with self.assertRaisesRegex(FollowupError, "archived"):
                await repository.create_turn(
                    thread_id=thread_id,
                    question="must fail",
                    model="fixture-chat-model",
                    prompt_version="followup-prompt-v1",
                    retriever_version="hybrid-rrf-v1",
                )
        finally:
            if thread_id:
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        "DELETE FROM followup_threads WHERE thread_id = %s",
                        (thread_id,),
                    )
            async with connection.cursor() as cursor:
                await cursor.execute("DELETE FROM idea_cases WHERE case_id = %s", (case_id,))
            await connection.commit()
            await connection.close()


if __name__ == "__main__":
    unittest.main()
