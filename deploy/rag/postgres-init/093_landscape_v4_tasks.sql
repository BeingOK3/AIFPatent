BEGIN;

CREATE TABLE IF NOT EXISTS landscape_v4_tasks (
    task_id TEXT PRIMARY KEY CHECK (task_id ~ '^TSK-[0-9a-f]{16}$'),
    run_id TEXT NOT NULL REFERENCES landscape_v4_runs(run_id) ON DELETE RESTRICT,
    task_key TEXT NOT NULL CHECK (length(btrim(task_key)) > 0),
    task_type TEXT NOT NULL CHECK (length(btrim(task_type)) > 0),
    payload_hash TEXT NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
    state TEXT NOT NULL CHECK (state IN ('QUEUED','LEASED','SUCCEEDED','UNRESOLVED')),
    attempts INTEGER NOT NULL CHECK (attempts >= 0),
    max_attempts INTEGER NOT NULL CHECK (max_attempts >= 1),
    lease_owner TEXT,
    lease_expires_at BIGINT,
    last_error TEXT,
    created_at BIGINT NOT NULL CHECK (created_at >= 0),
    updated_at BIGINT NOT NULL CHECK (updated_at >= created_at),
    UNIQUE(run_id,task_key),
    CHECK ((state = 'LEASED' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)
        OR state <> 'LEASED'),
    CHECK ((state = 'SUCCEEDED' AND last_error IS NULL) OR state <> 'SUCCEEDED')
);

CREATE INDEX IF NOT EXISTS idx_landscape_v4_tasks_claim
    ON landscape_v4_tasks(state,lease_expires_at,created_at);

INSERT INTO aifpatent_schema_migrations(version)
VALUES ('093_landscape_v4_tasks')
ON CONFLICT (version) DO NOTHING;

COMMIT;
