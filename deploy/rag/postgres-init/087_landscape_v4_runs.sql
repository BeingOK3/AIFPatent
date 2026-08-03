-- Formal Landscape v4 Runs can only bind confirmed immutable scope revisions.

BEGIN;

CREATE TABLE IF NOT EXISTS landscape_v4_runs (
    run_id TEXT PRIMARY KEY CHECK (run_id ~ '^LRN-[0-9a-f]{16}$'),
    scope_revision_id TEXT NOT NULL
        REFERENCES landscape_v4_scope_revisions(scope_revision_id) ON DELETE RESTRICT,
    scope_revision_hash TEXT NOT NULL CHECK (scope_revision_hash ~ '^[0-9a-f]{64}$'),
    taxonomy_version TEXT NOT NULL
        REFERENCES landscape_taxonomy_versions(taxonomy_version) ON DELETE RESTRICT,
    taxonomy_hash TEXT NOT NULL CHECK (taxonomy_hash ~ '^[0-9a-f]{64}$'),
    status TEXT NOT NULL CHECK (status IN (
        'PLANNING','ESTIMATING','AWAITING_SCALE_CONFIRMATION','READY','RUNNING',
        'WAITING_FOR_CREDENTIALS','COMPLETED','COMPLETED_WITH_LIMITATIONS',
        'FAILED','CANCELLED'
    )),
    mode TEXT NOT NULL CHECK (mode IN (
        'COMPANY_ONLY','TECHNOLOGY_ONLY','COMPANY_AND_TECHNOLOGY'
    )),
    publication_start DATE NOT NULL,
    publication_end DATE NOT NULL,
    workflow_version TEXT NOT NULL CHECK (workflow_version = 'landscape-v4/1.0.0'),
    created_at BIGINT NOT NULL CHECK (created_at >= 0),
    updated_at BIGINT NOT NULL CHECK (updated_at >= created_at),
    CONSTRAINT ck_landscape_v4_runs_date_range
        CHECK (publication_end >= publication_start),
    UNIQUE(run_id, scope_revision_id),
    UNIQUE(run_id, taxonomy_version)
);
CREATE INDEX IF NOT EXISTS idx_landscape_v4_runs_created
    ON landscape_v4_runs(created_at DESC,run_id DESC);
CREATE INDEX IF NOT EXISTS idx_landscape_v4_runs_status
    ON landscape_v4_runs(status,updated_at);

CREATE OR REPLACE FUNCTION prevent_landscape_v4_run_binding_mutation()
RETURNS trigger AS $$
BEGIN
    IF NEW.run_id IS DISTINCT FROM OLD.run_id
       OR NEW.scope_revision_id IS DISTINCT FROM OLD.scope_revision_id
       OR NEW.scope_revision_hash IS DISTINCT FROM OLD.scope_revision_hash
       OR NEW.taxonomy_version IS DISTINCT FROM OLD.taxonomy_version
       OR NEW.taxonomy_hash IS DISTINCT FROM OLD.taxonomy_hash
       OR NEW.mode IS DISTINCT FROM OLD.mode
       OR NEW.publication_start IS DISTINCT FROM OLD.publication_start
       OR NEW.publication_end IS DISTINCT FROM OLD.publication_end
       OR NEW.workflow_version IS DISTINCT FROM OLD.workflow_version
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'landscape v4 Run bindings are immutable';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_landscape_v4_run_binding_immutable
    ON landscape_v4_runs;
CREATE TRIGGER trg_landscape_v4_run_binding_immutable
BEFORE UPDATE ON landscape_v4_runs
FOR EACH ROW EXECUTE FUNCTION prevent_landscape_v4_run_binding_mutation();

INSERT INTO aifpatent_schema_migrations(version)
VALUES ('087_landscape_v4_runs')
ON CONFLICT (version) DO NOTHING;

COMMIT;
