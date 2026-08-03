-- Distinguish one-run exclusions from durable rejection and retirement.

BEGIN;

ALTER TABLE landscape_v4_scope_draft_names
    ADD COLUMN IF NOT EXISTS memory_action TEXT NOT NULL DEFAULT 'NONE';
ALTER TABLE landscape_v4_company_names
    ADD COLUMN IF NOT EXISTS memory_action TEXT NOT NULL DEFAULT 'NONE';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_landscape_v4_scope_draft_names_memory_action'
    ) THEN
        ALTER TABLE landscape_v4_scope_draft_names
            ADD CONSTRAINT ck_landscape_v4_scope_draft_names_memory_action
            CHECK (memory_action IN ('NONE','REJECT','RETIRE'));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_landscape_v4_company_names_memory_action'
    ) THEN
        ALTER TABLE landscape_v4_company_names
            ADD CONSTRAINT ck_landscape_v4_company_names_memory_action
            CHECK (memory_action IN ('NONE','REJECT','RETIRE'));
    END IF;

    ALTER TABLE landscape_v4_company_name_registry
        DROP CONSTRAINT IF EXISTS landscape_v4_company_name_registry_status_check;
    -- Older pre-release rows used EXCLUDED as a single ambiguous durable
    -- state. Preserve their conservative meaning as a remembered rejection.
    UPDATE landscape_v4_company_name_registry
    SET status = 'REJECTED'
    WHERE status = 'EXCLUDED';
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_landscape_v4_company_registry_status'
    ) THEN
        ALTER TABLE landscape_v4_company_name_registry
            ADD CONSTRAINT ck_landscape_v4_company_registry_status
            CHECK (status IN ('ACTIVE','REJECTED','RETIRED'));
    END IF;
END;
$$;

INSERT INTO aifpatent_schema_migrations(version)
VALUES ('086_landscape_v4_company_memory_actions')
ON CONFLICT (version) DO NOTHING;

COMMIT;
