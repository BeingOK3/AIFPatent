BEGIN;

ALTER TABLE landscape_v4_runs
    ADD COLUMN IF NOT EXISTS error_code TEXT,
    ADD COLUMN IF NOT EXISTS error_message TEXT,
    ADD COLUMN IF NOT EXISTS started_at BIGINT,
    ADD COLUMN IF NOT EXISTS completed_at BIGINT;

CREATE TABLE IF NOT EXISTS landscape_v4_run_stages (
    run_id TEXT NOT NULL,
    stage_name TEXT NOT NULL,
    stage_order INTEGER NOT NULL CHECK (stage_order > 0),
    status TEXT NOT NULL CHECK (status IN (
        'PENDING','RUNNING','WAITING','SUCCEEDED','SUCCEEDED_WITH_LIMITATIONS','FAILED','CANCELLED'
    )),
    attempt INTEGER NOT NULL CHECK (attempt >= 0),
    completed_count INTEGER NOT NULL CHECK (completed_count >= 0),
    total_count INTEGER CHECK (total_count IS NULL OR total_count >= 0),
    error_code TEXT,
    error_message TEXT,
    started_at BIGINT,
    completed_at BIGINT,
    updated_at BIGINT NOT NULL CHECK (updated_at >= 0),
    PRIMARY KEY(run_id,stage_name),
    UNIQUE(run_id,stage_order),
    FOREIGN KEY(run_id) REFERENCES landscape_v4_runs(run_id) ON DELETE RESTRICT,
    CHECK (total_count IS NULL OR completed_count <= total_count),
    CHECK ((status='FAILED' AND error_code IS NOT NULL)
        OR (status<>'FAILED'))
);

CREATE TABLE IF NOT EXISTS landscape_v4_run_limitations (
    run_id TEXT NOT NULL,
    limitation_id TEXT NOT NULL,
    stage_name TEXT NOT NULL,
    code TEXT NOT NULL,
    message TEXT NOT NULL,
    affected_count INTEGER CHECK (affected_count IS NULL OR affected_count >= 0),
    created_at BIGINT NOT NULL CHECK (created_at >= 0),
    PRIMARY KEY(run_id,limitation_id),
    FOREIGN KEY(run_id,stage_name)
        REFERENCES landscape_v4_run_stages(run_id,stage_name) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_landscape_v4_stages_status
    ON landscape_v4_run_stages(run_id,status,stage_order);

CREATE OR REPLACE FUNCTION prevent_landscape_v4_limitation_mutation()
RETURNS trigger AS $$ BEGIN RAISE EXCEPTION 'landscape v4 limitations are immutable'; END; $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_landscape_v4_run_limitations_immutable
    ON landscape_v4_run_limitations;
CREATE TRIGGER trg_landscape_v4_run_limitations_immutable
BEFORE UPDATE OR DELETE ON landscape_v4_run_limitations
FOR EACH ROW EXECUTE FUNCTION prevent_landscape_v4_limitation_mutation();

INSERT INTO aifpatent_schema_migrations(version)
VALUES ('097_landscape_v4_workflow_state')
ON CONFLICT (version) DO NOTHING;

COMMIT;
