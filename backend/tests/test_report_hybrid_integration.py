from __future__ import annotations

import asyncio
import os
import unittest
import uuid

from idea.agent_schemas import IdeaFeature
from idea.config import load_config
from idea.hybrid import HybridRetriever
from idea.postgres_corpus import PostgreSQLPatentChunkRepository
from idea.postgres_lexical import PostgreSQLLexicalSearchRepository
from idea.postgres_report import PostgreSQLReportScopeRepository
from idea.report_retrieval import InitialReportRetriever


@unittest.skipUnless(
    os.environ.get("AIFPATENT_RUN_REPORT_HYBRID_INTEGRATION") == "1",
    "set AIFPATENT_RUN_REPORT_HYBRID_INTEGRATION=1 to test real report Hybrid audit",
)
class ReportHybridIntegrationTests(unittest.TestCase):
    def test_real_postgres_hybrid_fallback_and_audit(self) -> None:
        asyncio.run(self._run_scenario())

    async def _run_scenario(self) -> None:
        import psycopg

        dsn = os.environ["AIFPATENT_POSTGRES_DSN"]
        connection = await psycopg.AsyncConnection.connect(dsn)
        case_id = "report-hybrid-case-" + uuid.uuid4().hex
        run_id = "report-hybrid-run-" + uuid.uuid4().hex
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT rdv.document_id, rdv.version_id, pd.publication_number
                    FROM run_document_versions rdv
                    JOIN patent_document_versions pv ON pv.version_id = rdv.version_id
                    JOIN patent_documents pd ON pd.document_id = rdv.document_id
                    WHERE rdv.corpus_availability = 'READY' AND pv.state = 'READY'
                      AND EXISTS (
                        SELECT 1 FROM patent_chunks c
                        WHERE c.version_id = rdv.version_id AND c.section_type = 'abstract'
                      )
                      AND EXISTS (
                        SELECT 1 FROM patent_chunks c
                        WHERE c.version_id = rdv.version_id AND c.section_type = 'claims'
                          AND (c.claim_kind = 'independent' OR c.claim_number = '1')
                      )
                    ORDER BY rdv.version_id LIMIT 1
                    """
                )
                row = await cursor.fetchone()
                self.assertIsNotNone(row, "integration database needs structured READY Corpus")
                document_id, version_id, publication = map(str, row)
                timestamp = 1_785_000_000_000
                await cursor.execute(
                    """INSERT INTO idea_cases(case_id, title, created_at, updated_at)
                       VALUES (%s, %s, %s, %s)""",
                    (case_id, case_id, timestamp, timestamp),
                )
                await cursor.execute(
                    """
                    INSERT INTO idea_runs(
                        run_id, case_id, status, evaluation_date, date_basis,
                        analysis_scope, model, skill_version, workflow_version,
                        config_snapshot, created_at, started_at
                    ) VALUES (
                        %s, %s, 'RUNNING', '2026-07-22', 'publication',
                        'integration', 'fixture', 'integration/1', 'integration/1',
                        '{}'::jsonb, %s, %s
                    )
                    """,
                    (run_id, case_id, timestamp, timestamp),
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
            await connection.commit()

            lexical = PostgreSQLLexicalSearchRepository(dsn)
            retriever = InitialReportRetriever(
                PostgreSQLReportScopeRepository(dsn),
                lexical,
                chunk_repository=PostgreSQLPatentChunkRepository(dsn),
                hybrid_search=HybridRetriever(lexical, load_config().rag.hybrid),
            )
            result = await retriever.retrieve(
                run_id=run_id,
                features=(IdeaFeature(
                    feature_id="F1",
                    feature_text=publication,
                    source_type="normalized",
                    required=True,
                ),),
                allowed_version_ids=(version_id,),
            )
            self.assertEqual(result.retriever_version, "hybrid-rrf-v1")
            self.assertIn("LEXICAL_ONLY", result.limitations)
            self.assertTrue(any(item.selection_reason == "hybrid" for item in result.selections))

            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT COUNT(*), COUNT(final_rank), COUNT(query_sources_json),
                           COUNT(*) FILTER (WHERE selection_reason = 'hybrid'),
                           COUNT(*) FILTER (WHERE vector_rank IS NOT NULL)
                    FROM report_retrieval_hits WHERE run_id = %s
                    """,
                    (run_id,),
                )
                counts = tuple(await cursor.fetchone())
            self.assertGreater(counts[0], 0)
            self.assertGreater(counts[1], 0)
            self.assertEqual(counts[0], counts[2])
            self.assertGreater(counts[3], 0)
            self.assertEqual(counts[4], 0)
        finally:
            async with connection.cursor() as cursor:
                await cursor.execute("DELETE FROM idea_cases WHERE case_id = %s", (case_id,))
            await connection.commit()
            await connection.close()


if __name__ == "__main__":
    unittest.main()
