BEGIN;

ALTER TABLE landscape_v4_publications
    ADD COLUMN IF NOT EXISTS application_number TEXT,
    ADD COLUMN IF NOT EXISTS snippet TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS priority_date DATE,
    ADD COLUMN IF NOT EXISTS filing_date DATE,
    ADD COLUMN IF NOT EXISTS assignee TEXT;

CREATE TABLE IF NOT EXISTS landscape_v4_family_manifests (
    run_id TEXT PRIMARY KEY,
    algorithm_version TEXT NOT NULL,
    publication_count INTEGER NOT NULL CHECK (publication_count >= 0),
    analysis_unit_count INTEGER NOT NULL CHECK (analysis_unit_count >= 0),
    resolution_hash TEXT NOT NULL CHECK (resolution_hash ~ '^[0-9a-f]{64}$'),
    FOREIGN KEY(run_id) REFERENCES landscape_v4_publication_sets(run_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS landscape_v4_analysis_units (
    run_id TEXT NOT NULL,
    analysis_unit_id TEXT NOT NULL CHECK (analysis_unit_id ~ '^AU-[0-9a-f]{16}$'),
    merge_basis TEXT NOT NULL CHECK (merge_basis IN (
        'SAME_APPLICATION','EXACT_SIMPLE_PRIORITY_SET','CONSERVATIVE_SINGLETON'
    )),
    sort_order INTEGER NOT NULL CHECK (sort_order > 0),
    PRIMARY KEY(run_id,analysis_unit_id),
    UNIQUE(run_id,sort_order),
    FOREIGN KEY(run_id) REFERENCES landscape_v4_family_manifests(run_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS landscape_v4_analysis_unit_members (
    run_id TEXT NOT NULL,
    analysis_unit_id TEXT NOT NULL,
    publication_id TEXT NOT NULL,
    sort_order INTEGER NOT NULL CHECK (sort_order > 0),
    PRIMARY KEY(run_id,analysis_unit_id,publication_id),
    UNIQUE(run_id,publication_id),
    UNIQUE(run_id,analysis_unit_id,sort_order),
    FOREIGN KEY(run_id,analysis_unit_id)
        REFERENCES landscape_v4_analysis_units(run_id,analysis_unit_id) ON DELETE RESTRICT,
    FOREIGN KEY(run_id,publication_id)
        REFERENCES landscape_v4_publications(run_id,publication_id) ON DELETE RESTRICT
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'landscape_v4_direction_records_analysis_unit_fkey'
    ) THEN
        ALTER TABLE landscape_v4_direction_records
            ADD CONSTRAINT landscape_v4_direction_records_analysis_unit_fkey
            FOREIGN KEY(run_id,analysis_unit_id)
            REFERENCES landscape_v4_analysis_units(run_id,analysis_unit_id)
            ON DELETE RESTRICT;
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION prevent_landscape_v4_family_mutation()
RETURNS trigger AS $$ BEGIN RAISE EXCEPTION 'landscape v4 family results are immutable'; END; $$ LANGUAGE plpgsql;

DO $$
DECLARE table_name TEXT;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'landscape_v4_family_manifests','landscape_v4_analysis_units',
        'landscape_v4_analysis_unit_members'
    ] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_%s_immutable ON %I',table_name,table_name);
        EXECUTE format('CREATE TRIGGER trg_%s_immutable BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION prevent_landscape_v4_family_mutation()',table_name,table_name);
    END LOOP;
END;
$$;

INSERT INTO aifpatent_schema_migrations(version)
VALUES ('098_landscape_v4_analysis_units')
ON CONFLICT (version) DO NOTHING;

COMMIT;
