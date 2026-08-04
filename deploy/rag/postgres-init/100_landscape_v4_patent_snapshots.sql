BEGIN;

CREATE TABLE IF NOT EXISTS landscape_v4_patent_snapshots (
    run_id TEXT NOT NULL,
    publication_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('AVAILABLE','PROVIDER_FAILED')),
    publication_number TEXT,
    application_number TEXT,
    family_id TEXT,
    title TEXT NOT NULL,
    priority_date DATE,
    filing_date DATE,
    publication_date DATE,
    url TEXT NOT NULL,
    language TEXT NOT NULL,
    abstract_text TEXT NOT NULL,
    provider TEXT NOT NULL,
    failure_reason TEXT,
    content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    sort_order INTEGER NOT NULL CHECK (sort_order > 0),
    PRIMARY KEY(run_id,publication_id),
    UNIQUE(run_id,sort_order),
    FOREIGN KEY(run_id,publication_id)
        REFERENCES landscape_v4_publications(run_id,publication_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS landscape_v4_patent_snapshot_applicants (
    run_id TEXT NOT NULL,
    publication_id TEXT NOT NULL,
    applicant_name TEXT NOT NULL,
    sort_order INTEGER NOT NULL CHECK (sort_order > 0),
    PRIMARY KEY(run_id,publication_id,sort_order),
    UNIQUE(run_id,publication_id,applicant_name),
    FOREIGN KEY(run_id,publication_id)
        REFERENCES landscape_v4_patent_snapshots(run_id,publication_id) ON DELETE RESTRICT
);

CREATE OR REPLACE FUNCTION prevent_landscape_v4_snapshot_mutation()
RETURNS trigger AS $$ BEGIN RAISE EXCEPTION 'landscape v4 patent snapshots are immutable'; END; $$ LANGUAGE plpgsql;

DO $$
DECLARE table_name TEXT;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'landscape_v4_patent_snapshots','landscape_v4_patent_snapshot_applicants'
    ] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_%s_immutable ON %I',table_name,table_name);
        EXECUTE format('CREATE TRIGGER trg_%s_immutable BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION prevent_landscape_v4_snapshot_mutation()',table_name,table_name);
    END LOOP;
END;
$$;

INSERT INTO aifpatent_schema_migrations(version)
VALUES ('100_landscape_v4_patent_snapshots')
ON CONFLICT (version) DO NOTHING;

COMMIT;
