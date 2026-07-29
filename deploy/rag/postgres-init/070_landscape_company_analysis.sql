-- Landscape company technology analysis schema.
-- This migration creates the complete durable boundary up front.  The
-- application enables the repositories incrementally without changing an
-- already-applied migration.

BEGIN;

CREATE TABLE IF NOT EXISTS landscape_candidates (
    run_id TEXT NOT NULL REFERENCES landscape_runs(run_id) ON DELETE CASCADE,
    document_id TEXT NOT NULL,
    publication_number TEXT NOT NULL,
    normalized_key TEXT NOT NULL,
    rank INTEGER NOT NULL CHECK (rank >= 1),
    decision TEXT NOT NULL CHECK (decision = 'ELIGIBLE'),
    metadata_json JSONB NOT NULL,
    content_hash TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    PRIMARY KEY(run_id, document_id),
    UNIQUE(run_id, publication_number),
    UNIQUE(run_id, normalized_key),
    UNIQUE(run_id, rank)
);

CREATE TABLE IF NOT EXISTS landscape_companies (
    run_id TEXT NOT NULL REFERENCES landscape_runs(run_id) ON DELETE CASCADE,
    company_id TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    aliases_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    raw_names_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    resolution_source TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    content_hash TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    PRIMARY KEY(run_id, company_id)
);

CREATE TABLE IF NOT EXISTS landscape_document_companies (
    run_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    company_id TEXT NOT NULL,
    relationship TEXT NOT NULL CHECK (relationship IN ('PRIMARY','CO_ASSIGNEE')),
    observed_assignee TEXT,
    matched_alias TEXT,
    assignment_status TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    content_hash TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    PRIMARY KEY(run_id, document_id, company_id, relationship),
    FOREIGN KEY(run_id, document_id)
        REFERENCES landscape_candidates(run_id, document_id) ON DELETE CASCADE,
    FOREIGN KEY(run_id, company_id)
        REFERENCES landscape_companies(run_id, company_id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_landscape_document_primary_company
    ON landscape_document_companies(run_id, document_id)
    WHERE relationship = 'PRIMARY';

CREATE TABLE IF NOT EXISTS landscape_company_categories (
    run_id TEXT NOT NULL,
    company_id TEXT NOT NULL,
    category_id TEXT NOT NULL,
    name TEXT NOT NULL,
    summary TEXT NOT NULL,
    keywords_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    content_hash TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    PRIMARY KEY(run_id, company_id, category_id),
    FOREIGN KEY(run_id, company_id)
        REFERENCES landscape_companies(run_id, company_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS landscape_company_category_members (
    run_id TEXT NOT NULL,
    company_id TEXT NOT NULL,
    category_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    publication_number TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    PRIMARY KEY(run_id, document_id),
    FOREIGN KEY(run_id, company_id, category_id)
        REFERENCES landscape_company_categories(run_id, company_id, category_id)
        ON DELETE CASCADE,
    FOREIGN KEY(run_id, document_id)
        REFERENCES landscape_candidates(run_id, document_id) ON DELETE CASCADE,
    UNIQUE(run_id, publication_number)
);

CREATE TABLE IF NOT EXISTS landscape_company_profiles (
    run_id TEXT NOT NULL,
    company_id TEXT NOT NULL,
    profile_json JSONB NOT NULL,
    content_hash TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    PRIMARY KEY(run_id, company_id),
    FOREIGN KEY(run_id, company_id)
        REFERENCES landscape_companies(run_id, company_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS landscape_cross_company_trends (
    run_id TEXT NOT NULL REFERENCES landscape_runs(run_id) ON DELETE CASCADE,
    trend_id TEXT NOT NULL,
    trend_json JSONB NOT NULL,
    content_hash TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    PRIMARY KEY(run_id, trend_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_landscape_evidence_run_identity
    ON landscape_evidence(run_id, evidence_id);

CREATE TABLE IF NOT EXISTS landscape_insight_evidence (
    run_id TEXT NOT NULL REFERENCES landscape_runs(run_id) ON DELETE CASCADE,
    insight_type TEXT NOT NULL CHECK (insight_type IN ('CATEGORY','PROFILE','TREND')),
    insight_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    PRIMARY KEY(run_id, insight_type, insight_id, evidence_id),
    FOREIGN KEY(run_id, evidence_id)
        REFERENCES landscape_evidence(run_id, evidence_id) ON DELETE RESTRICT,
    FOREIGN KEY(run_id, document_id)
        REFERENCES landscape_candidates(run_id, document_id) ON DELETE CASCADE
);

INSERT INTO aifpatent_schema_migrations(version)
VALUES ('070_landscape_company_analysis')
ON CONFLICT (version) DO NOTHING;

COMMIT;
