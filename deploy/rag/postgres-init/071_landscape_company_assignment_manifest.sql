-- Freeze each run's company assignment set, including a legitimately empty set.

BEGIN;

CREATE TABLE IF NOT EXISTS landscape_company_assignment_manifests (
    run_id TEXT PRIMARY KEY REFERENCES landscape_runs(run_id) ON DELETE CASCADE,
    company_count INTEGER NOT NULL CHECK (company_count >= 0),
    assignment_count INTEGER NOT NULL CHECK (assignment_count >= 0),
    content_hash TEXT NOT NULL,
    created_at BIGINT NOT NULL
);

INSERT INTO aifpatent_schema_migrations(version)
VALUES ('071_landscape_company_assignment_manifest')
ON CONFLICT (version) DO NOTHING;

COMMIT;
