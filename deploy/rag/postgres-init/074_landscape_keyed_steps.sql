-- Scope workflow attempts to one main, company, or publication task.

BEGIN;

ALTER TABLE landscape_steps
    ADD COLUMN IF NOT EXISTS task_key TEXT NOT NULL DEFAULT '__main__';

ALTER TABLE landscape_steps
    DROP CONSTRAINT IF EXISTS landscape_steps_run_id_step_name_attempt_key;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'landscape_steps'::regclass
          AND conname = 'landscape_steps_run_step_task_attempt_key'
    ) THEN
        ALTER TABLE landscape_steps
            ADD CONSTRAINT landscape_steps_run_step_task_attempt_key
            UNIQUE(run_id, step_name, task_key, attempt);
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_landscape_steps_task
    ON landscape_steps(run_id, step_name, task_key, attempt DESC);

INSERT INTO aifpatent_schema_migrations(version)
VALUES ('074_landscape_keyed_steps')
ON CONFLICT (version) DO NOTHING;

COMMIT;
