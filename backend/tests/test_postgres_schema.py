from __future__ import annotations

import unittest
from pathlib import Path


SCHEMA_PATH = Path("deploy/rag/postgres-init/010_core_schema.sql")
CORPUS_SCHEMA_PATH = Path("deploy/rag/postgres-init/020_corpus_schema.sql")
LEXICAL_SCHEMA_PATH = Path("deploy/rag/postgres-init/030_lexical_schema.sql")
REPORT_RETRIEVAL_SCHEMA_PATH = Path("deploy/rag/postgres-init/035_report_retrieval_schema.sql")
REPORT_CITATION_SCHEMA_PATH = Path("deploy/rag/postgres-init/040_report_citation_schema.sql")
FOLLOWUP_SCHEMA_PATH = Path("deploy/rag/postgres-init/050_followup_schema.sql")
REPORT_HYBRID_SCHEMA_PATH = Path("deploy/rag/postgres-init/055_report_hybrid_schema.sql")
LANDSCAPE_COMPANY_SCHEMA_PATH = Path(
    "deploy/rag/postgres-init/070_landscape_company_analysis.sql"
)
LANDSCAPE_COMPANY_MANIFEST_SCHEMA_PATH = Path(
    "deploy/rag/postgres-init/071_landscape_company_assignment_manifest.sql"
)
LANDSCAPE_FETCH_SCHEMA_PATH = Path(
    "deploy/rag/postgres-init/072_landscape_document_fetches.sql"
)
LANDSCAPE_RESULT_MANIFEST_SCHEMA_PATH = Path(
    "deploy/rag/postgres-init/073_landscape_company_result_manifests.sql"
)
LANDSCAPE_KEYED_STEPS_SCHEMA_PATH = Path(
    "deploy/rag/postgres-init/074_landscape_keyed_steps.sql"
)
LANDSCAPE_REPAIR_SNAPSHOTS_SCHEMA_PATH = Path(
    "deploy/rag/postgres-init/075_landscape_repair_snapshots.sql"
)


class PostgreSQLSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sql = SCHEMA_PATH.read_text(encoding="utf-8")
        self.corpus_sql = CORPUS_SCHEMA_PATH.read_text(encoding="utf-8")
        self.lexical_sql = LEXICAL_SCHEMA_PATH.read_text(encoding="utf-8")
        self.report_retrieval_sql = REPORT_RETRIEVAL_SCHEMA_PATH.read_text(encoding="utf-8")
        self.report_citation_sql = REPORT_CITATION_SCHEMA_PATH.read_text(encoding="utf-8")
        self.followup_sql = FOLLOWUP_SCHEMA_PATH.read_text(encoding="utf-8")
        self.report_hybrid_sql = REPORT_HYBRID_SCHEMA_PATH.read_text(encoding="utf-8")
        self.landscape_company_sql = LANDSCAPE_COMPANY_SCHEMA_PATH.read_text(
            encoding="utf-8"
        )
        self.landscape_company_manifest_sql = (
            LANDSCAPE_COMPANY_MANIFEST_SCHEMA_PATH.read_text(encoding="utf-8")
        )
        self.landscape_fetch_sql = LANDSCAPE_FETCH_SCHEMA_PATH.read_text(
            encoding="utf-8"
        )
        self.landscape_result_manifest_sql = (
            LANDSCAPE_RESULT_MANIFEST_SCHEMA_PATH.read_text(encoding="utf-8")
        )
        self.landscape_keyed_steps_sql = (
            LANDSCAPE_KEYED_STEPS_SCHEMA_PATH.read_text(encoding="utf-8")
        )
        self.landscape_repair_snapshots_sql = (
            LANDSCAPE_REPAIR_SNAPSHOTS_SCHEMA_PATH.read_text(encoding="utf-8")
        )

    def test_bridge_schema_is_idempotent_and_has_required_tables(self) -> None:
        self.assertIn("BEGIN;", self.sql)
        self.assertIn("COMMIT;", self.sql)
        for table in (
            "aifpatent_schema_migrations",
            "idea_cases",
            "idea_runs",
            "run_inputs",
            "run_steps",
            "patent_documents",
            "evidence",
            "reports",
            "model_context_manifests",
        ):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", self.sql)
        self.assertIn("ON CONFLICT (version) DO NOTHING", self.sql)

    def test_schema_preserves_immutability_and_json_boundaries(self) -> None:
        self.assertIn("CREATE OR REPLACE FUNCTION prevent_immutable_run_fields_update", self.sql)
        self.assertIn("CREATE OR REPLACE FUNCTION prevent_run_input_update", self.sql)
        self.assertGreaterEqual(self.sql.count("JSONB"), 15)
        self.assertIn("allowed_version_ids_json JSONB NOT NULL", self.sql)
        self.assertIn("purpose TEXT NOT NULL CHECK", self.sql)

    def test_schema_has_no_data_destructive_commands(self) -> None:
        upper = self.sql.upper()
        self.assertNotIn("DROP TABLE", upper)
        self.assertNotIn("TRUNCATE", upper)
        self.assertNotIn("DELETE FROM", upper)

    def test_company_assignment_manifest_freezes_empty_and_nonempty_sets(self) -> None:
        sql = self.landscape_company_manifest_sql
        self.assertIn(
            "CREATE TABLE IF NOT EXISTS landscape_company_assignment_manifests",
            sql,
        )
        self.assertIn("company_count INTEGER NOT NULL", sql)
        self.assertIn("assignment_count INTEGER NOT NULL", sql)
        self.assertIn("content_hash TEXT NOT NULL", sql)
        self.assertIn("'071_landscape_company_assignment_manifest'", sql)
        upper = sql.upper()
        self.assertNotIn("DROP TABLE", upper)
        self.assertNotIn("TRUNCATE", upper)
        self.assertNotIn("DELETE FROM", upper)

    def test_landscape_document_fetch_state_is_resumable_and_versioned(self) -> None:
        sql = self.landscape_fetch_sql
        self.assertIn(
            "CREATE TABLE IF NOT EXISTS landscape_document_fetches",
            sql,
        )
        self.assertIn("status IN ('FETCHED','FAILED')", sql)
        self.assertIn("attempt_count INTEGER NOT NULL", sql)
        self.assertIn("document_json JSONB", sql)
        self.assertIn("REFERENCES landscape_candidates(run_id, document_id)", sql)
        self.assertIn("'072_landscape_document_fetches'", sql)
        upper = sql.upper()
        self.assertNotIn("DROP TABLE", upper)
        self.assertNotIn("TRUNCATE", upper)
        self.assertNotIn("DELETE FROM", upper)

    def test_landscape_company_result_manifests_cover_zero_trends_and_audits(self) -> None:
        sql = self.landscape_result_manifest_sql
        for table in (
            "landscape_company_analysis_manifests",
            "landscape_cross_company_analyses",
            "landscape_coverage_audits",
        ):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", sql)
        self.assertIn("trend_count INTEGER NOT NULL CHECK (trend_count >= 0)", sql)
        self.assertIn("PRIMARY KEY(run_id, repair_round)", sql)
        self.assertIn(
            "decision IN ('PASS','REPAIR','LIMITED','FAIL')",
            sql,
        )
        self.assertIn("'073_landscape_company_result_manifests'", sql)
        upper = sql.upper()
        self.assertNotIn("DROP TABLE", upper)
        self.assertNotIn("TRUNCATE", upper)
        self.assertNotIn("DELETE FROM", upper)
        self.assertNotIn("POSTGRES_PASSWORD=", upper)

    def test_landscape_step_attempts_are_scoped_by_stable_task_key(self) -> None:
        sql = self.landscape_keyed_steps_sql
        self.assertIn(
            "ADD COLUMN IF NOT EXISTS task_key TEXT NOT NULL DEFAULT '__main__'",
            " ".join(sql.split()),
        )
        self.assertIn(
            "UNIQUE(run_id, step_name, task_key, attempt)",
            sql,
        )
        self.assertIn("idx_landscape_steps_task", sql)
        self.assertIn("'074_landscape_keyed_steps'", sql)
        upper = sql.upper()
        self.assertNotIn("DROP TABLE", upper)
        self.assertNotIn("TRUNCATE", upper)
        self.assertNotIn("DELETE FROM", upper)

    def test_landscape_repair_snapshots_are_append_only_and_versioned(self) -> None:
        sql = self.landscape_repair_snapshots_sql
        self.assertIn(
            "CREATE TABLE IF NOT EXISTS landscape_company_profile_revisions", sql
        )
        self.assertIn(
            "CREATE TABLE IF NOT EXISTS landscape_cross_company_analysis_revisions",
            sql,
        )
        self.assertIn("PRIMARY KEY(run_id, repair_round, company_id)", sql)
        self.assertIn("PRIMARY KEY(run_id, repair_round)", sql)
        self.assertIn("'075_landscape_repair_snapshots'", sql)
        upper = sql.upper()
        self.assertNotIn("DROP TABLE", upper)
        self.assertNotIn("TRUNCATE", upper)
        self.assertNotIn("DELETE FROM", upper)

    def test_corpus_schema_is_versioned_and_scoped(self) -> None:
        self.assertIn("BEGIN;", self.corpus_sql)
        self.assertIn("COMMIT;", self.corpus_sql)
        for table in (
            "corpus_blobs",
            "patent_document_versions",
            "patent_version_sources",
            "run_document_versions",
            "patent_chunks",
            "embedding_profiles",
            "embedding_vectors",
            "chunk_embeddings",
            "report_retrieval_hits",
        ):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", self.corpus_sql)
        self.assertIn("INSERT INTO aifpatent_schema_migrations(version)", self.corpus_sql)
        self.assertIn("'020_corpus_schema'", self.corpus_sql)
        self.assertIn("REFERENCES patent_document_versions(version_id)", self.corpus_sql)
        self.assertIn("REFERENCES corpus_blobs(blob_hash)", self.corpus_sql)

    def test_corpus_schema_has_immutability_checks_and_no_destructive_commands(self) -> None:
        upper = self.corpus_sql.upper()
        self.assertIn("NORMALIZED_CONTENT_HASH TEXT NOT NULL", upper)
        self.assertIn("TEXT_HASH TEXT NOT NULL", upper)
        self.assertIn("UNIQUE(DOCUMENT_ID, LANGUAGE, NORMALIZED_CONTENT_HASH)", upper)
        self.assertIn("PRIMARY KEY(RUN_ID, DOCUMENT_ID)", upper)
        self.assertNotIn("DROP TABLE", upper)
        self.assertNotIn("TRUNCATE", upper)
        self.assertNotIn("DELETE FROM", upper)

    def test_corpus_schema_has_generated_fts_and_trigram_indexes(self) -> None:
        self.assertIn("BEGIN;", self.lexical_sql)
        self.assertIn("search_terms TEXT NOT NULL DEFAULT ''", self.lexical_sql)
        self.assertIn("search_tsv TSVECTOR GENERATED ALWAYS AS", self.lexical_sql)
        self.assertIn("to_tsvector('english'::regconfig", self.lexical_sql)
        self.assertIn("to_tsvector('simple'::regconfig", self.lexical_sql)
        self.assertIn("USING GIN (search_tsv)", self.lexical_sql)
        self.assertIn("gin_trgm_ops", self.lexical_sql)
        self.assertIn("'030_lexical_schema'", self.lexical_sql)
        self.assertNotIn("DROP TABLE", self.lexical_sql.upper())

    def test_report_retrieval_schema_audits_zero_hit_feature_version_pairs(self) -> None:
        sql = self.report_retrieval_sql
        self.assertIn("CREATE TABLE IF NOT EXISTS report_retrieval_queries", sql)
        self.assertIn("UNIQUE(run_id, version_id, feature_id, retriever_version)", sql)
        self.assertIn("hit_count INTEGER NOT NULL CHECK (hit_count >= 0)", sql)
        self.assertIn("'035_report_retrieval_schema'", sql)
        self.assertNotIn("DROP TABLE", sql.upper())
        self.assertNotIn("TRUNCATE", sql.upper())
        self.assertNotIn("DELETE FROM", sql.upper())

    def test_report_citation_schema_binds_only_model_cited_chunks(self) -> None:
        sql = self.report_citation_sql
        self.assertIn("CREATE TABLE IF NOT EXISTS report_model_citations", sql)
        self.assertIn("context_id TEXT NOT NULL REFERENCES model_context_manifests", sql)
        self.assertIn("chunk_id TEXT NOT NULL REFERENCES patent_chunks", sql)
        self.assertIn("ADD COLUMN IF NOT EXISTS lexical_score", sql)
        self.assertIn("'040_report_citation_schema'", sql)
        self.assertNotIn("DELETE FROM", sql.upper())

    def test_followup_schema_is_scoped_append_only_and_versioned(self) -> None:
        sql = self.followup_sql
        for table in (
            "followup_threads",
            "followup_turns",
            "followup_retrieval_hits",
            "followup_citations",
        ):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", sql)
        self.assertIn("REFERENCES idea_runs(run_id) ON DELETE CASCADE", sql)
        self.assertIn("REFERENCES patent_chunks(chunk_id) ON DELETE RESTRICT", sql)
        self.assertIn("REFERENCES followup_retrieval_hits", sql)
        self.assertIn("prevent_followup_thread_scope_update", sql)
        self.assertIn("enforce_followup_turn_transition", sql)
        self.assertIn("terminal follow-up turn is immutable", sql)
        self.assertIn("fk_model_context_followup_turn", sql)
        self.assertIn("'050_followup_schema'", sql)
        self.assertNotIn("TRUNCATE", sql.upper())
        self.assertNotIn("DELETE FROM", sql.upper())

    def test_report_hybrid_schema_records_explainable_fusion_coordinates(self) -> None:
        sql = self.report_hybrid_sql
        self.assertIn("'hybrid'", sql)
        self.assertIn("ADD COLUMN IF NOT EXISTS final_rank", sql)
        self.assertIn("ADD COLUMN IF NOT EXISTS section_weight", sql)
        self.assertIn("ADD COLUMN IF NOT EXISTS final_score", sql)
        self.assertIn("query_sources_json JSONB", sql)
        self.assertIn("'055_report_hybrid_schema'", sql)
        self.assertNotIn("DROP TABLE", sql.upper())
        self.assertNotIn("TRUNCATE", sql.upper())
        self.assertNotIn("DELETE FROM", sql.upper())

    def test_landscape_company_schema_is_complete_versioned_and_non_destructive(self) -> None:
        sql = self.landscape_company_sql
        for table in (
            "landscape_candidates",
            "landscape_companies",
            "landscape_document_companies",
            "landscape_company_categories",
            "landscape_company_category_members",
            "landscape_company_profiles",
            "landscape_cross_company_trends",
            "landscape_insight_evidence",
        ):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", sql)
        self.assertIn("UNIQUE(run_id, publication_number)", sql)
        self.assertIn("UNIQUE(run_id, normalized_key)", sql)
        self.assertIn("metadata_json JSONB NOT NULL", sql)
        self.assertIn(
            "REFERENCES landscape_evidence(run_id, evidence_id)",
            " ".join(sql.split()),
        )
        self.assertIn("'070_landscape_company_analysis'", sql)
        self.assertIn("ON CONFLICT (version) DO NOTHING", sql)
        upper = sql.upper()
        self.assertNotIn("DROP TABLE", upper)
        self.assertNotIn("TRUNCATE", upper)
        self.assertNotIn("DELETE FROM", upper)


if __name__ == "__main__":
    unittest.main()
