-- AIFPatent PostgreSQL bridge schema.
-- IDs and millisecond timestamps remain text/bigint in this first migration so
-- a read-only SQLite export can be compared before a production cut-over.

BEGIN;

CREATE TABLE IF NOT EXISTS aifpatent_schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS idea_cases (
    case_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL,
    archived_at BIGINT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_idea_cases_title_ci ON idea_cases (lower(title));

CREATE TABLE IF NOT EXISTS idea_runs (
    run_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES idea_cases(case_id) ON DELETE CASCADE,
    parent_run_id TEXT REFERENCES idea_runs(run_id) ON DELETE SET NULL,
    status TEXT NOT NULL CHECK (status IN ('QUEUED','RUNNING','COMPLETED','COMPLETED_WITH_LIMITATIONS','FAILED','CANCELLED')),
    evaluation_date TEXT NOT NULL,
    date_basis TEXT NOT NULL,
    analysis_scope TEXT NOT NULL,
    model TEXT NOT NULL,
    skill_version TEXT NOT NULL,
    workflow_version TEXT NOT NULL,
    config_snapshot JSONB NOT NULL,
    limitation_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at BIGINT NOT NULL,
    started_at BIGINT,
    completed_at BIGINT,
    error_code TEXT,
    error_message TEXT
);
CREATE INDEX IF NOT EXISTS idx_idea_runs_case_created ON idea_runs(case_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_idea_runs_status ON idea_runs(status);

CREATE TABLE IF NOT EXISTS run_inputs (
    run_id TEXT PRIMARY KEY REFERENCES idea_runs(run_id) ON DELETE CASCADE,
    input_text TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    attachments_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    settings_json JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS run_steps (
    step_id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES idea_runs(run_id) ON DELETE CASCADE,
    step_name TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    status TEXT NOT NULL,
    input_hash TEXT,
    output_hash TEXT,
    output_json JSONB,
    error_code TEXT,
    error_message TEXT,
    started_at BIGINT,
    completed_at BIGINT,
    UNIQUE(run_id, step_name, attempt)
);
CREATE INDEX IF NOT EXISTS idx_run_steps_run ON run_steps(run_id, step_id);

CREATE TABLE IF NOT EXISTS stage_results (
    run_id TEXT NOT NULL REFERENCES idea_runs(run_id) ON DELETE CASCADE,
    stage_name TEXT NOT NULL,
    result_json JSONB NOT NULL,
    content_hash TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    PRIMARY KEY(run_id, stage_name)
);

CREATE TABLE IF NOT EXISTS tool_calls (
    call_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES idea_runs(run_id) ON DELETE CASCADE,
    step_name TEXT NOT NULL,
    provider TEXT NOT NULL,
    operation TEXT NOT NULL,
    request_json JSONB NOT NULL,
    response_summary_json JSONB,
    result_count INTEGER,
    duration_ms INTEGER,
    status TEXT NOT NULL,
    error_code TEXT,
    error_message TEXT,
    created_at BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS search_queries (
    query_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES idea_runs(run_id) ON DELETE CASCADE,
    round_number INTEGER NOT NULL,
    query_type TEXT NOT NULL,
    language TEXT NOT NULL,
    query_text TEXT NOT NULL,
    rationale TEXT,
    created_at BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS search_hits (
    hit_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES idea_runs(run_id) ON DELETE CASCADE,
    query_id TEXT REFERENCES search_queries(query_id) ON DELETE SET NULL,
    provider TEXT NOT NULL,
    provider_rank INTEGER,
    title TEXT,
    url TEXT,
    publication_number TEXT,
    application_number TEXT,
    family_id TEXT,
    snippet TEXT,
    raw_json JSONB NOT NULL,
    normalized_key TEXT,
    created_at BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_search_hits_run ON search_hits(run_id);
CREATE INDEX IF NOT EXISTS idx_search_hits_publication ON search_hits(publication_number);

CREATE TABLE IF NOT EXISTS patent_families (
    family_id TEXT PRIMARY KEY,
    canonical_publication_number TEXT,
    source TEXT,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS patent_documents (
    document_id TEXT PRIMARY KEY,
    publication_number TEXT NOT NULL,
    application_number TEXT,
    family_id TEXT REFERENCES patent_families(family_id) ON DELETE SET NULL,
    title TEXT,
    assignee TEXT,
    inventors_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    priority_date TEXT,
    filing_date TEXT,
    publication_date TEXT,
    grant_date TEXT,
    language TEXT,
    url TEXT,
    abstract_text TEXT,
    claims_text TEXT,
    description_text TEXT,
    content_hash TEXT,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL,
    UNIQUE(publication_number, language)
);

CREATE TABLE IF NOT EXISTS run_documents (
    run_id TEXT NOT NULL REFERENCES idea_runs(run_id) ON DELETE CASCADE,
    document_id TEXT NOT NULL REFERENCES patent_documents(document_id) ON DELETE RESTRICT,
    relevance TEXT,
    relevance_score DOUBLE PRECISION,
    screening_status TEXT NOT NULL DEFAULT 'CANDIDATE',
    deep_reviewed BOOLEAN NOT NULL DEFAULT false,
    found_by_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    query_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    PRIMARY KEY(run_id, document_id)
);

CREATE TABLE IF NOT EXISTS idea_features (
    feature_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES idea_runs(run_id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    feature_text TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_start INTEGER,
    source_end INTEGER,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE(run_id, ordinal)
);

CREATE TABLE IF NOT EXISTS evidence (
    evidence_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES idea_runs(run_id) ON DELETE CASCADE,
    document_id TEXT NOT NULL REFERENCES patent_documents(document_id) ON DELETE RESTRICT,
    section_type TEXT NOT NULL,
    section_label TEXT NOT NULL,
    quote_text TEXT NOT NULL,
    start_offset INTEGER,
    end_offset INTEGER,
    content_hash TEXT NOT NULL,
    created_at BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS feature_mappings (
    mapping_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES idea_runs(run_id) ON DELETE CASCADE,
    document_id TEXT NOT NULL REFERENCES patent_documents(document_id) ON DELETE RESTRICT,
    feature_id TEXT NOT NULL REFERENCES idea_features(feature_id) ON DELETE CASCADE,
    coverage_status TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL,
    evidence_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    rationale TEXT NOT NULL,
    UNIQUE(run_id, document_id, feature_id)
);

CREATE TABLE IF NOT EXISTS novelty_results (
    run_id TEXT PRIMARY KEY REFERENCES idea_runs(run_id) ON DELETE CASCADE,
    conclusion TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL,
    destroying_document_id TEXT REFERENCES patent_documents(document_id) ON DELETE SET NULL,
    matrix_json JSONB NOT NULL,
    rationale TEXT NOT NULL,
    created_at BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS inventive_routes (
    route_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES idea_runs(run_id) ON DELETE CASCADE,
    d1_document_id TEXT NOT NULL REFERENCES patent_documents(document_id) ON DELETE RESTRICT,
    d2_document_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    status TEXT NOT NULL,
    result_json JSONB NOT NULL,
    created_at BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS value_results (
    run_id TEXT PRIMARY KEY REFERENCES idea_runs(run_id) ON DELETE CASCADE,
    result_json JSONB NOT NULL,
    created_at BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_results (
    audit_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES idea_runs(run_id) ON DELETE CASCADE,
    severity TEXT NOT NULL,
    code TEXT NOT NULL,
    message TEXT NOT NULL,
    details_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS reports (
    run_id TEXT PRIMARY KEY REFERENCES idea_runs(run_id) ON DELETE CASCADE,
    report_json_path TEXT NOT NULL,
    report_json_hash TEXT NOT NULL,
    report_md_path TEXT NOT NULL,
    report_md_hash TEXT NOT NULL,
    manifest_path TEXT NOT NULL,
    created_at BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES idea_runs(run_id) ON DELETE CASCADE,
    artifact_type TEXT NOT NULL,
    path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_circuit_breakers (
    provider TEXT PRIMARY KEY,
    state TEXT NOT NULL CHECK (state IN ('OPEN')),
    reason TEXT NOT NULL,
    error_code TEXT NOT NULL,
    blocked_until BIGINT NOT NULL,
    updated_at BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS deletion_events (
    event_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    operator_label TEXT,
    deleted_at BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_context_manifests (
    context_id TEXT PRIMARY KEY,
    purpose TEXT NOT NULL CHECK (purpose IN ('INITIAL_REVIEW','FOLLOWUP')),
    run_id TEXT NOT NULL REFERENCES idea_runs(run_id) ON DELETE CASCADE,
    turn_id TEXT,
    agent_name TEXT NOT NULL,
    context_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    retriever_version TEXT NOT NULL,
    token_counter_version TEXT NOT NULL,
    corpus_snapshot_hash TEXT NOT NULL,
    allowed_version_ids_json JSONB NOT NULL,
    selected_chunk_ids_json JSONB NOT NULL,
    citation_bindings_json JSONB NOT NULL,
    budget_json JSONB NOT NULL,
    omissions_json JSONB NOT NULL,
    limitations_json JSONB NOT NULL,
    manifest_json JSONB NOT NULL,
    context_hash TEXT NOT NULL,
    created_at BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_context_manifests_run ON model_context_manifests(run_id, created_at);
CREATE INDEX IF NOT EXISTS idx_context_manifests_turn ON model_context_manifests(turn_id, created_at);

CREATE OR REPLACE FUNCTION prevent_immutable_run_fields_update()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.case_id IS DISTINCT FROM NEW.case_id
       OR OLD.parent_run_id IS DISTINCT FROM NEW.parent_run_id
       OR OLD.evaluation_date IS DISTINCT FROM NEW.evaluation_date
       OR OLD.date_basis IS DISTINCT FROM NEW.date_basis
       OR OLD.analysis_scope IS DISTINCT FROM NEW.analysis_scope
       OR OLD.model IS DISTINCT FROM NEW.model
       OR OLD.skill_version IS DISTINCT FROM NEW.skill_version
       OR OLD.workflow_version IS DISTINCT FROM NEW.workflow_version
       OR OLD.config_snapshot IS DISTINCT FROM NEW.config_snapshot
       OR OLD.created_at IS DISTINCT FROM NEW.created_at
    THEN
        RAISE EXCEPTION 'immutable run fields cannot be updated';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS prevent_run_immutable_update ON idea_runs;
CREATE TRIGGER prevent_run_immutable_update
BEFORE UPDATE ON idea_runs
FOR EACH ROW EXECUTE FUNCTION prevent_immutable_run_fields_update();

CREATE OR REPLACE FUNCTION prevent_run_input_update()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'run input is immutable';
END;
$$;

DROP TRIGGER IF EXISTS prevent_run_input_update ON run_inputs;
CREATE TRIGGER prevent_run_input_update
BEFORE UPDATE ON run_inputs
FOR EACH ROW EXECUTE FUNCTION prevent_run_input_update();

INSERT INTO aifpatent_schema_migrations(version)
VALUES ('010_core_schema')
ON CONFLICT (version) DO NOTHING;

COMMIT;
