-- Append-only repair snapshots. Base company/trend results remain immutable.

BEGIN;

CREATE TABLE IF NOT EXISTS landscape_company_profile_revisions (
    run_id TEXT NOT NULL REFERENCES landscape_runs(run_id) ON DELETE CASCADE,
    repair_round INTEGER NOT NULL CHECK (repair_round >= 1),
    company_id TEXT NOT NULL,
    profile_json JSONB NOT NULL,
    content_hash TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    PRIMARY KEY(run_id, repair_round, company_id),
    FOREIGN KEY(run_id, company_id)
        REFERENCES landscape_companies(run_id, company_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS landscape_cross_company_analysis_revisions (
    run_id TEXT NOT NULL REFERENCES landscape_runs(run_id) ON DELETE CASCADE,
    repair_round INTEGER NOT NULL CHECK (repair_round >= 1),
    analysis_json JSONB NOT NULL,
    trend_count INTEGER NOT NULL CHECK (trend_count >= 0),
    content_hash TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    PRIMARY KEY(run_id, repair_round)
);

INSERT INTO aifpatent_schema_migrations(version)
VALUES ('075_landscape_repair_snapshots')
ON CONFLICT (version) DO NOTHING;

COMMIT;
