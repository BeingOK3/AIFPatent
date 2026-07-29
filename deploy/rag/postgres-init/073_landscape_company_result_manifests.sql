-- Durable completion markers for company profiles, zero-trend analyses, and audits.

BEGIN;

CREATE TABLE IF NOT EXISTS landscape_company_analysis_manifests (
    run_id TEXT NOT NULL,
    company_id TEXT NOT NULL,
    category_count INTEGER NOT NULL CHECK (category_count >= 1),
    member_count INTEGER NOT NULL CHECK (member_count >= 1),
    content_hash TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    PRIMARY KEY(run_id, company_id),
    FOREIGN KEY(run_id, company_id)
        REFERENCES landscape_companies(run_id, company_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS landscape_cross_company_analyses (
    run_id TEXT PRIMARY KEY REFERENCES landscape_runs(run_id) ON DELETE CASCADE,
    analysis_json JSONB NOT NULL,
    trend_count INTEGER NOT NULL CHECK (trend_count >= 0),
    content_hash TEXT NOT NULL,
    created_at BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS landscape_coverage_audits (
    run_id TEXT NOT NULL REFERENCES landscape_runs(run_id) ON DELETE CASCADE,
    repair_round INTEGER NOT NULL CHECK (repair_round >= 0),
    decision TEXT NOT NULL CHECK (decision IN ('PASS','REPAIR','LIMITED','FAIL')),
    audit_json JSONB NOT NULL,
    content_hash TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    PRIMARY KEY(run_id, repair_round)
);

INSERT INTO aifpatent_schema_migrations(version)
VALUES ('073_landscape_company_result_manifests')
ON CONFLICT (version) DO NOTHING;

COMMIT;
