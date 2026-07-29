-- Unified PostgreSQL runtime tables.
-- IDEA/corpus/follow-up tables are created by 010..055.  This migration adds
-- the Landscape bounded-context tables, cache metadata and durable workflow
-- coordination records to the same database.

BEGIN;

CREATE TABLE IF NOT EXISTS cache_entries (
    cache_key TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    path TEXT NOT NULL,
    size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
    content_hash TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    sequence BIGINT NOT NULL UNIQUE,
    lease_count INTEGER NOT NULL DEFAULT 0 CHECK (lease_count >= 0)
);
CREATE INDEX IF NOT EXISTS idx_cache_fifo ON cache_entries(sequence);

CREATE TABLE IF NOT EXISTS workflow_runs (
    run_id TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    status TEXT NOT NULL,
    state_version BIGINT NOT NULL DEFAULT 0,
    idempotency_key TEXT,
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_workflow_runs_idempotency
    ON workflow_runs(idempotency_key) WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS workflow_events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE CASCADE,
    sequence BIGINT NOT NULL,
    event_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    payload_hash TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    UNIQUE(run_id, sequence)
);
CREATE INDEX IF NOT EXISTS idx_workflow_events_run
    ON workflow_events(run_id, sequence);

CREATE TABLE IF NOT EXISTS workflow_leases (
    run_id TEXT PRIMARY KEY REFERENCES workflow_runs(run_id) ON DELETE CASCADE,
    owner TEXT NOT NULL,
    lease_id TEXT NOT NULL UNIQUE,
    expires_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS workflow_idempotency (
    idempotency_key TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    result JSONB,
    created_at BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS landscape_runs (
    run_id TEXT PRIMARY KEY,
    parent_run_id TEXT REFERENCES landscape_runs(run_id) ON DELETE SET NULL,
    status TEXT NOT NULL CHECK (status IN ('QUEUED','RUNNING','COMPLETED','COMPLETED_WITH_LIMITATIONS','FAILED','CANCELLED')),
    mode TEXT NOT NULL CHECK (mode IN ('TECHNOLOGY','COMPETITOR','TECHNOLOGY_COMPETITOR')),
    publication_start TEXT NOT NULL,
    publication_end TEXT NOT NULL,
    model TEXT NOT NULL,
    workflow_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    scope_json JSONB NOT NULL,
    config_snapshot JSONB NOT NULL,
    input_hash TEXT NOT NULL,
    limitation_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at BIGINT NOT NULL,
    started_at BIGINT,
    completed_at BIGINT,
    error_code TEXT,
    error_message TEXT
);
CREATE INDEX IF NOT EXISTS idx_landscape_runs_created
    ON landscape_runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_landscape_runs_status
    ON landscape_runs(status);

CREATE TABLE IF NOT EXISTS landscape_steps (
    step_id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES landscape_runs(run_id) ON DELETE CASCADE,
    step_name TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1,
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
CREATE INDEX IF NOT EXISTS idx_landscape_steps_run
    ON landscape_steps(run_id, step_id);

CREATE TABLE IF NOT EXISTS landscape_stage_results (
    run_id TEXT NOT NULL REFERENCES landscape_runs(run_id) ON DELETE CASCADE,
    stage_name TEXT NOT NULL,
    result_json JSONB NOT NULL,
    content_hash TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    PRIMARY KEY(run_id, stage_name)
);

CREATE TABLE IF NOT EXISTS landscape_queries (
    query_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES landscape_runs(run_id) ON DELETE CASCADE,
    query_text TEXT NOT NULL,
    language TEXT NOT NULL,
    rationale TEXT NOT NULL,
    created_at BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS landscape_hits (
    hit_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES landscape_runs(run_id) ON DELETE CASCADE,
    query_id TEXT REFERENCES landscape_queries(query_id) ON DELETE SET NULL,
    provider TEXT NOT NULL,
    publication_number TEXT,
    application_number TEXT,
    publication_date TEXT,
    assignee TEXT,
    normalized_key TEXT,
    decision TEXT NOT NULL,
    exclusion_reason TEXT,
    raw_json JSONB NOT NULL,
    created_at BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_landscape_hits_run ON landscape_hits(run_id);

CREATE TABLE IF NOT EXISTS landscape_run_documents (
    run_id TEXT NOT NULL REFERENCES landscape_runs(run_id) ON DELETE CASCADE,
    document_id TEXT NOT NULL,
    publication_number TEXT NOT NULL,
    status TEXT NOT NULL,
    metadata_json JSONB NOT NULL,
    error_code TEXT,
    error_message TEXT,
    PRIMARY KEY(run_id, document_id),
    UNIQUE(run_id, publication_number)
);

CREATE TABLE IF NOT EXISTS landscape_evidence (
    evidence_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES landscape_runs(run_id) ON DELETE CASCADE,
    document_id TEXT NOT NULL,
    section_type TEXT NOT NULL,
    section_label TEXT NOT NULL,
    quote_text TEXT NOT NULL,
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    created_at BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS landscape_patent_analyses (
    run_id TEXT NOT NULL REFERENCES landscape_runs(run_id) ON DELETE CASCADE,
    document_id TEXT NOT NULL,
    publication_number TEXT NOT NULL,
    analysis_json JSONB NOT NULL,
    content_hash TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    PRIMARY KEY(run_id, document_id)
);

CREATE TABLE IF NOT EXISTS landscape_clusters (
    cluster_id TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES landscape_runs(run_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    summary TEXT NOT NULL,
    keywords_json JSONB NOT NULL,
    PRIMARY KEY(run_id, cluster_id)
);

CREATE TABLE IF NOT EXISTS landscape_cluster_members (
    run_id TEXT NOT NULL,
    cluster_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    publication_number TEXT NOT NULL,
    PRIMARY KEY(run_id, document_id),
    FOREIGN KEY(run_id, cluster_id)
        REFERENCES landscape_clusters(run_id, cluster_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS landscape_reports (
    run_id TEXT PRIMARY KEY REFERENCES landscape_runs(run_id) ON DELETE CASCADE,
    report_json_path TEXT NOT NULL,
    report_json_hash TEXT NOT NULL,
    report_md_path TEXT NOT NULL,
    report_md_hash TEXT NOT NULL,
    patents_csv_path TEXT NOT NULL,
    patents_csv_hash TEXT NOT NULL,
    manifest_path TEXT NOT NULL,
    created_at BIGINT NOT NULL
);

INSERT INTO aifpatent_schema_migrations(version)
VALUES ('060_unified_runtime_schema')
ON CONFLICT (version) DO NOTHING;

COMMIT;
