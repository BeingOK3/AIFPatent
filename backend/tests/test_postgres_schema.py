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
LANDSCAPE_TAXONOMY_SCHEMA_PATH = Path(
    "deploy/rag/postgres-init/080_landscape_taxonomy.sql"
)
LANDSCAPE_V4_SCOPE_SCHEMA_PATH = Path(
    "deploy/rag/postgres-init/081_landscape_v4_scope.sql"
)
LANDSCAPE_V4_COMPANY_REGISTRY_SCHEMA_PATH = Path(
    "deploy/rag/postgres-init/082_landscape_v4_company_registry.sql"
)
LANDSCAPE_V4_PROFILE_VERSIONING_SCHEMA_PATH = Path(
    "deploy/rag/postgres-init/083_landscape_v4_profile_versioning.sql"
)
LANDSCAPE_V4_COMPANY_NAME_ORDER_SCHEMA_PATH = Path(
    "deploy/rag/postgres-init/084_landscape_v4_company_name_order.sql"
)
LANDSCAPE_V4_SCOPE_LIMITATIONS_SCHEMA_PATH = Path(
    "deploy/rag/postgres-init/085_landscape_v4_scope_limitations.sql"
)
LANDSCAPE_V4_COMPANY_MEMORY_ACTIONS_SCHEMA_PATH = Path(
    "deploy/rag/postgres-init/086_landscape_v4_company_memory_actions.sql"
)
LANDSCAPE_V4_RUNS_SCHEMA_PATH = Path(
    "deploy/rag/postgres-init/087_landscape_v4_runs.sql"
)
LANDSCAPE_V4_QUERY_PLANS_SCHEMA_PATH = Path(
    "deploy/rag/postgres-init/088_landscape_v4_query_plans.sql"
)
LANDSCAPE_V4_SEARCH_PAGES_SCHEMA_PATH = Path(
    "deploy/rag/postgres-init/089_landscape_v4_search_pages.sql"
)
LANDSCAPE_V4_PUBLICATIONS_SCHEMA_PATH = Path(
    "deploy/rag/postgres-init/090_landscape_v4_publications.sql"
)
LANDSCAPE_V4_SCALE_GATE_SCHEMA_PATH = Path(
    "deploy/rag/postgres-init/091_landscape_v4_scale_gate.sql"
)
LANDSCAPE_V4_ABSTRACT_SCHEMA_PATH = Path(
    "deploy/rag/postgres-init/092_landscape_v4_abstract_evidence.sql"
)
LANDSCAPE_V4_TASKS_SCHEMA_PATH = Path(
    "deploy/rag/postgres-init/093_landscape_v4_tasks.sql"
)
LANDSCAPE_V4_DIRECTION_SCHEMA_PATH = Path(
    "deploy/rag/postgres-init/094_landscape_v4_direction_classification.sql"
)
LANDSCAPE_V4_OTHERS_SCHEMA_PATH = Path(
    "deploy/rag/postgres-init/095_landscape_v4_others.sql"
)
LANDSCAPE_V4_ANALYTICS_SCHEMA_PATH = Path(
    "deploy/rag/postgres-init/096_landscape_v4_metrics_trends.sql"
)
LANDSCAPE_V4_WORKFLOW_SCHEMA_PATH = Path(
    "deploy/rag/postgres-init/097_landscape_v4_workflow_state.sql"
)
LANDSCAPE_V4_ANALYSIS_UNITS_SCHEMA_PATH = Path(
    "deploy/rag/postgres-init/098_landscape_v4_analysis_units.sql"
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
        self.landscape_others_sql = LANDSCAPE_V4_OTHERS_SCHEMA_PATH.read_text(
            encoding="utf-8"
        )
        self.landscape_analytics_sql = LANDSCAPE_V4_ANALYTICS_SCHEMA_PATH.read_text(
            encoding="utf-8"
        )
        self.landscape_workflow_sql = LANDSCAPE_V4_WORKFLOW_SCHEMA_PATH.read_text(
            encoding="utf-8"
        )
        self.landscape_analysis_units_sql = (
            LANDSCAPE_V4_ANALYSIS_UNITS_SCHEMA_PATH.read_text(encoding="utf-8")
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
        self.landscape_taxonomy_sql = LANDSCAPE_TAXONOMY_SCHEMA_PATH.read_text(
            encoding="utf-8"
        )
        self.landscape_v4_scope_sql = LANDSCAPE_V4_SCOPE_SCHEMA_PATH.read_text(
            encoding="utf-8"
        )
        self.landscape_v4_company_registry_sql = (
            LANDSCAPE_V4_COMPANY_REGISTRY_SCHEMA_PATH.read_text(encoding="utf-8")
        )
        self.landscape_v4_profile_versioning_sql = (
            LANDSCAPE_V4_PROFILE_VERSIONING_SCHEMA_PATH.read_text(encoding="utf-8")
        )
        self.landscape_v4_company_name_order_sql = (
            LANDSCAPE_V4_COMPANY_NAME_ORDER_SCHEMA_PATH.read_text(encoding="utf-8")
        )
        self.landscape_v4_scope_limitations_sql = (
            LANDSCAPE_V4_SCOPE_LIMITATIONS_SCHEMA_PATH.read_text(encoding="utf-8")
        )
        self.landscape_v4_company_memory_actions_sql = (
            LANDSCAPE_V4_COMPANY_MEMORY_ACTIONS_SCHEMA_PATH.read_text(encoding="utf-8")
        )
        self.landscape_v4_runs_sql = LANDSCAPE_V4_RUNS_SCHEMA_PATH.read_text(
            encoding="utf-8"
        )
        self.landscape_v4_query_plans_sql = (
            LANDSCAPE_V4_QUERY_PLANS_SCHEMA_PATH.read_text(encoding="utf-8")
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

    def test_landscape_taxonomy_is_versioned_relational_and_immutable(self) -> None:
        sql = self.landscape_taxonomy_sql
        for table in (
            "landscape_taxonomy_versions",
            "landscape_taxonomy_categories",
        ):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", sql)
        self.assertIn("PRIMARY KEY(taxonomy_version, category_id)", sql)
        self.assertIn("FOREIGN KEY(taxonomy_version, parent_id)", sql)
        self.assertIn("prevent_landscape_taxonomy_mutation", sql)
        self.assertIn("BEFORE UPDATE OR DELETE", sql)
        self.assertIn("'080_landscape_taxonomy'", sql)
        upper = sql.upper()
        self.assertNotIn("DROP TABLE", upper)
        self.assertNotIn("TRUNCATE", upper)
        self.assertNotIn("DELETE FROM", upper)

    def test_landscape_v4_scope_is_reviewable_versioned_and_clean_slate(self) -> None:
        sql = self.landscape_v4_scope_sql
        for table in (
            "landscape_v4_company_profiles",
            "landscape_v4_company_profile_versions",
            "landscape_v4_company_names",
            "landscape_v4_scope_drafts",
            "landscape_v4_scope_draft_revisions",
            "landscape_v4_scope_draft_companies",
            "landscape_v4_scope_draft_names",
            "landscape_v4_scope_draft_terms",
            "landscape_v4_scope_revisions",
            "landscape_v4_scope_companies",
            "landscape_v4_scope_company_names",
            "landscape_v4_scope_terms",
        ):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", sql)
        self.assertIn("'COMPANY_ONLY','TECHNOLOGY_ONLY','COMPANY_AND_TECHNOLOGY'", sql)
        self.assertIn("'LEGAL_NAME','TRANSLATION','ALIAS','FORMER_NAME','SUBSIDIARY','GROUP_MEMBER'", sql)
        self.assertIn("'PROPOSED','ACTIVE','EXCLUDED'", sql)
        self.assertIn("publication_start DATE NOT NULL", sql)
        self.assertIn("CHECK (publication_end >= publication_start)", sql)
        self.assertIn("fk_landscape_v4_profile_version_scope_revision", sql)
        self.assertIn("fk_landscape_v4_scope_draft_confirmed_revision", sql)
        self.assertIn("DEFERRABLE INITIALLY DEFERRED", sql)
        self.assertIn("prevent_landscape_v4_snapshot_mutation", sql)
        self.assertIn("'081_landscape_v4_scope'", sql)
        upper = sql.upper()
        self.assertNotIn("DROP TABLE", upper)
        self.assertNotIn("TRUNCATE", upper)
        self.assertNotIn("DELETE FROM", upper)

    def test_landscape_v4_company_name_registry_has_atomic_unique_ownership(self) -> None:
        sql = self.landscape_v4_company_registry_sql
        self.assertIn(
            "CREATE TABLE IF NOT EXISTS landscape_v4_company_name_registry", sql
        )
        self.assertIn("normalized_text TEXT PRIMARY KEY", sql)
        self.assertIn("FOREIGN KEY(profile_id, profile_version)", sql)
        self.assertIn("status IN ('ACTIVE','REJECTED','RETIRED')", sql)
        self.assertIn("'082_landscape_v4_company_registry'", sql)
        upper = sql.upper()
        self.assertNotIn("DROP TABLE", upper)
        self.assertNotIn("TRUNCATE", upper)
        self.assertNotIn("DELETE FROM", upper)

    def test_landscape_v4_company_memory_actions_are_explicit_and_additive(self) -> None:
        sql = self.landscape_v4_company_memory_actions_sql
        self.assertIn("ADD COLUMN IF NOT EXISTS memory_action", sql)
        self.assertIn("'NONE','REJECT','RETIRE'", sql)
        self.assertIn("'ACTIVE','REJECTED','RETIRED'", sql)
        self.assertIn("'086_landscape_v4_company_memory_actions'", sql)
        upper = sql.upper()
        self.assertNotIn("DROP TABLE", upper)
        self.assertNotIn("TRUNCATE", upper)
        self.assertNotIn("DELETE FROM", upper)

    def test_landscape_v4_runs_require_immutable_scope_and_taxonomy_bindings(self) -> None:
        sql = self.landscape_v4_runs_sql
        self.assertIn("CREATE TABLE IF NOT EXISTS landscape_v4_runs", sql)
        self.assertIn("REFERENCES landscape_v4_scope_revisions", sql)
        self.assertIn("REFERENCES landscape_taxonomy_versions", sql)
        self.assertIn("workflow_version = 'landscape-v4/1.0.0'", sql)
        self.assertIn("prevent_landscape_v4_run_binding_mutation", sql)
        self.assertIn("'087_landscape_v4_runs'", sql)
        upper = sql.upper()
        self.assertNotIn("DROP TABLE", upper)
        self.assertNotIn("TRUNCATE", upper)
        self.assertNotIn("DELETE FROM", upper)

    def test_landscape_v4_query_plans_are_relational_and_immutable(self) -> None:
        sql = self.landscape_v4_query_plans_sql
        for table in (
            "landscape_v4_query_plans",
            "landscape_v4_search_queries",
            "landscape_v4_search_query_terms",
        ):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", sql)
        self.assertIn("prevent_landscape_v4_query_plan_mutation", sql)
        self.assertIn("FOREIGN KEY(run_id,scope_revision_id)", sql)
        self.assertIn("'088_landscape_v4_query_plans'", sql)

    def test_landscape_v4_search_pages_are_immutable_checkpoints(self) -> None:
        sql = Path("deploy/rag/postgres-init/089_landscape_v4_search_pages.sql").read_text()
        self.assertIn("landscape_v4_search_pages", sql)
        self.assertIn("PRIMARY KEY(run_id,query_id,page_number)", sql)
        self.assertIn("BEFORE UPDATE OR DELETE", sql)
        self.assertIn("'089_landscape_v4_search_pages'", sql)

    def test_landscape_v4_publications_are_relational_and_immutable(self) -> None:
        sql = LANDSCAPE_V4_PUBLICATIONS_SCHEMA_PATH.read_text(encoding="utf-8")
        for table in (
            "landscape_v4_publication_sets",
            "landscape_v4_publications",
            "landscape_v4_publication_sources",
        ):
            self.assertIn(table, sql)
        self.assertIn("UNIQUE(run_id,publication_identity)", sql)
        self.assertIn("BEFORE UPDATE OR DELETE", sql)
        self.assertIn("'090_landscape_v4_publications'", sql)

    def test_landscape_v4_scale_gate_has_one_way_decision(self) -> None:
        sql = LANDSCAPE_V4_SCALE_GATE_SCHEMA_PATH.read_text(encoding="utf-8")
        self.assertIn("landscape_v4_scale_gates", sql)
        self.assertIn("OLD.decision IS NOT NULL", sql)
        self.assertIn("'APPROVED','REJECTED'", sql)
        self.assertIn("'091_landscape_v4_scale_gate'", sql)

    def test_landscape_v4_abstract_evidence_is_sentence_scoped_and_immutable(self) -> None:
        sql = LANDSCAPE_V4_ABSTRACT_SCHEMA_PATH.read_text(encoding="utf-8")
        self.assertIn("landscape_v4_abstract_evidence", sql)
        self.assertIn("landscape_v4_abstract_sentences", sql)
        self.assertIn("BEFORE UPDATE OR DELETE", sql)
        self.assertIn("'092_landscape_v4_abstract_evidence'", sql)

    def test_landscape_v4_tasks_have_lease_and_idempotency_invariants(self) -> None:
        sql = LANDSCAPE_V4_TASKS_SCHEMA_PATH.read_text(encoding="utf-8")
        self.assertIn("landscape_v4_tasks", sql)
        self.assertIn("UNIQUE(run_id,task_key)", sql)
        self.assertIn("'LEASED'", sql)
        self.assertIn("'093_landscape_v4_tasks'", sql)

    def test_landscape_v4_direction_and_classification_results_are_immutable(self) -> None:
        sql = LANDSCAPE_V4_DIRECTION_SCHEMA_PATH.read_text(encoding="utf-8")
        self.assertIn("landscape_v4_direction_records", sql)
        self.assertIn("landscape_v4_classification_results", sql)
        self.assertIn("landscape_v4_direction_values", sql)
        self.assertIn("BEFORE UPDATE OR DELETE", sql)
        self.assertIn("'094_landscape_v4_direction_classification'", sql)
        upper = sql.upper()
        self.assertNotIn("DROP TABLE", upper)
        self.assertNotIn("TRUNCATE", upper)
        self.assertNotIn("DELETE FROM", upper)

    def test_landscape_v4_others_clusters_are_partitioned_and_immutable(self) -> None:
        sql = self.landscape_others_sql
        self.assertIn("landscape_v4_others_manifests", sql)
        self.assertIn("landscape_v4_others_clusters", sql)
        self.assertIn("landscape_v4_others_members", sql)
        self.assertIn("UNIQUE(run_id, analysis_unit_id)", sql)
        self.assertIn("BEFORE UPDATE OR DELETE", sql)
        self.assertIn("'095_landscape_v4_others'", sql)
        upper = sql.upper()
        self.assertNotIn("DROP TABLE", upper)
        self.assertNotIn("TRUNCATE", upper)
        self.assertNotIn("DELETE FROM", upper)

    def test_landscape_v4_analytics_are_relational_recomputable_and_immutable(self) -> None:
        sql = self.landscape_analytics_sql
        for table in (
            "landscape_v4_metric_manifests",
            "landscape_v4_metric_buckets",
            "landscape_v4_metric_cells",
            "landscape_v4_metric_cell_values",
            "landscape_v4_trend_manifests",
            "landscape_v4_trend_candidates",
            "landscape_v4_trend_bucket_metrics",
            "landscape_v4_trend_values",
            "landscape_v4_representative_manifests",
            "landscape_v4_representatives",
        ):
            self.assertIn(table, sql)
        self.assertIn("BEFORE UPDATE OR DELETE", sql)
        self.assertIn("'096_landscape_v4_metrics_trends'", sql)
        upper = sql.upper()
        self.assertNotIn("DROP TABLE", upper)
        self.assertNotIn("TRUNCATE", upper)
        self.assertNotIn("DELETE FROM", upper)

    def test_landscape_v4_workflow_state_is_resumable_and_auditable(self) -> None:
        sql = self.landscape_workflow_sql
        self.assertIn("landscape_v4_run_stages", sql)
        self.assertIn("landscape_v4_run_limitations", sql)
        self.assertIn("completed_count", sql)
        self.assertIn("error_code", sql)
        self.assertIn("BEFORE UPDATE OR DELETE", sql)
        self.assertIn("'097_landscape_v4_workflow_state'", sql)
        upper = sql.upper()
        self.assertNotIn("DROP TABLE", upper)
        self.assertNotIn("TRUNCATE", upper)
        self.assertNotIn("DELETE FROM", upper)

    def test_landscape_v4_analysis_units_strictly_partition_frozen_publications(self) -> None:
        sql = self.landscape_analysis_units_sql
        self.assertIn("landscape_v4_family_manifests", sql)
        self.assertIn("landscape_v4_analysis_units", sql)
        self.assertIn("landscape_v4_analysis_unit_members", sql)
        self.assertIn("UNIQUE(run_id,publication_id)", sql)
        self.assertIn("landscape_v4_direction_records_analysis_unit_fkey", sql)
        self.assertIn("'098_landscape_v4_analysis_units'", sql)
        upper = sql.upper()
        self.assertNotIn("DROP TABLE", upper)
        self.assertNotIn("TRUNCATE", upper)
        self.assertNotIn("DELETE FROM", upper)

    def test_landscape_v4_company_versions_allow_restoring_historical_content(self) -> None:
        sql = self.landscape_v4_profile_versioning_sql
        self.assertIn("ALTER TABLE landscape_v4_company_profile_versions", sql)
        self.assertIn("DROP CONSTRAINT IF EXISTS", sql)
        self.assertIn(
            "landscape_v4_company_profile_versi_profile_id_snapshot_hash_key", sql
        )
        self.assertIn("'083_landscape_v4_profile_versioning'", sql)
        self.assertNotIn("DROP TABLE", sql.upper())

    def test_landscape_v4_company_name_order_is_explicit_and_convergent(self) -> None:
        self.assertIn(
            "sort_order INTEGER NOT NULL CHECK (sort_order > 0)",
            self.landscape_v4_scope_sql,
        )
        sql = self.landscape_v4_company_name_order_sql
        self.assertIn("ADD COLUMN IF NOT EXISTS sort_order INTEGER", sql)
        self.assertIn("row_number() OVER", sql)
        self.assertIn("ALTER COLUMN sort_order SET NOT NULL", sql)
        self.assertIn("UNIQUE(profile_id,profile_version,sort_order)", sql)
        self.assertIn("'084_landscape_v4_company_name_order'", sql)
        self.assertNotIn("DROP TABLE", sql.upper())

    def test_landscape_v4_scope_limitations_are_versioned_and_immutable(self) -> None:
        sql = self.landscape_v4_scope_limitations_sql
        self.assertIn(
            "CREATE TABLE IF NOT EXISTS landscape_v4_scope_draft_limitations", sql
        )
        self.assertIn("PRIMARY KEY(draft_id,draft_revision,code,object_key)", sql)
        self.assertIn("prevent_landscape_v4_snapshot_mutation", sql)
        self.assertIn("'085_landscape_v4_scope_limitations'", sql)
        self.assertNotIn("DROP TABLE", sql.upper())

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
