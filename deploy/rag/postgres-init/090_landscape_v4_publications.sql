BEGIN;

CREATE TABLE IF NOT EXISTS landscape_v4_publication_sets (
    run_id TEXT PRIMARY KEY REFERENCES landscape_v4_runs(run_id) ON DELETE RESTRICT,
    freeze_hash TEXT NOT NULL CHECK (freeze_hash ~ '^[0-9a-f]{64}$'),
    publication_count INTEGER NOT NULL CHECK (publication_count >= 0),
    analysis_unit_count INTEGER NOT NULL CHECK (analysis_unit_count >= 0),
    created_at BIGINT NOT NULL CHECK (created_at >= 0)
);

CREATE TABLE IF NOT EXISTS landscape_v4_publications (
    run_id TEXT NOT NULL REFERENCES landscape_v4_publication_sets(run_id) ON DELETE RESTRICT,
    publication_id TEXT NOT NULL CHECK (publication_id ~ '^PUB-[0-9a-f]{16}$'),
    publication_identity TEXT NOT NULL,
    publication_number TEXT,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    publication_date DATE,
    family_id TEXT,
    provider TEXT NOT NULL,
    content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    sort_order INTEGER NOT NULL CHECK (sort_order > 0),
    PRIMARY KEY(run_id,publication_id),
    UNIQUE(run_id,publication_identity),
    UNIQUE(run_id,sort_order)
);

CREATE TABLE IF NOT EXISTS landscape_v4_publication_sources (
    run_id TEXT NOT NULL,
    publication_id TEXT NOT NULL,
    query_id TEXT NOT NULL,
    PRIMARY KEY(run_id,publication_id,query_id),
    FOREIGN KEY(run_id,publication_id)
        REFERENCES landscape_v4_publications(run_id,publication_id) ON DELETE RESTRICT,
    FOREIGN KEY(run_id,query_id)
        REFERENCES landscape_v4_search_queries(run_id,query_id) ON DELETE RESTRICT
);

CREATE OR REPLACE FUNCTION prevent_landscape_v4_publication_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'landscape v4 frozen publications are immutable';
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE table_name TEXT;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'landscape_v4_publication_sets',
        'landscape_v4_publications',
        'landscape_v4_publication_sources'
    ] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_%s_immutable ON %I', table_name, table_name);
        EXECUTE format(
            'CREATE TRIGGER trg_%s_immutable BEFORE UPDATE OR DELETE ON %I '
            'FOR EACH ROW EXECUTE FUNCTION prevent_landscape_v4_publication_mutation()',
            table_name, table_name
        );
    END LOOP;
END;
$$;

INSERT INTO aifpatent_schema_migrations(version)
VALUES ('090_landscape_v4_publications')
ON CONFLICT (version) DO NOTHING;

COMMIT;
