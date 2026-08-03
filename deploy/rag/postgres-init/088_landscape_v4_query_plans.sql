-- Immutable, replayable v4 search query plans.

BEGIN;

CREATE TABLE IF NOT EXISTS landscape_v4_query_plans (
    run_id TEXT PRIMARY KEY REFERENCES landscape_v4_runs(run_id) ON DELETE RESTRICT,
    scope_revision_id TEXT NOT NULL,
    scope_revision_hash TEXT NOT NULL CHECK (scope_revision_hash ~ '^[0-9a-f]{64}$'),
    plan_hash TEXT NOT NULL CHECK (plan_hash ~ '^[0-9a-f]{64}$'),
    query_count INTEGER NOT NULL CHECK (query_count > 0),
    created_at BIGINT NOT NULL CHECK (created_at >= 0),
    UNIQUE(run_id,plan_hash),
    FOREIGN KEY(run_id,scope_revision_id)
        REFERENCES landscape_v4_runs(run_id,scope_revision_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS landscape_v4_search_queries (
    run_id TEXT NOT NULL REFERENCES landscape_v4_query_plans(run_id) ON DELETE RESTRICT,
    query_id TEXT NOT NULL CHECK (query_id ~ '^LQ4-[0-9a-f]{16}$'),
    query_hash TEXT NOT NULL CHECK (query_hash ~ '^[0-9a-f]{64}$'),
    mode TEXT NOT NULL CHECK (mode IN (
        'COMPANY_ONLY','TECHNOLOGY_ONLY','COMPANY_AND_TECHNOLOGY'
    )),
    company_profile_id TEXT,
    company_name_id TEXT,
    company_name TEXT,
    publication_start DATE NOT NULL,
    publication_end DATE NOT NULL,
    query_text TEXT NOT NULL CHECK (length(btrim(query_text)) > 0),
    sort_order INTEGER NOT NULL CHECK (sort_order > 0),
    created_at BIGINT NOT NULL CHECK (created_at >= 0),
    PRIMARY KEY(run_id,query_id),
    UNIQUE(run_id,query_hash),
    UNIQUE(run_id,sort_order),
    CHECK (publication_end >= publication_start),
    CHECK (
        (company_profile_id IS NULL AND company_name_id IS NULL AND company_name IS NULL)
        OR
        (company_profile_id IS NOT NULL AND company_name_id IS NOT NULL AND company_name IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS landscape_v4_search_query_terms (
    run_id TEXT NOT NULL,
    query_id TEXT NOT NULL,
    term_id TEXT NOT NULL CHECK (term_id ~ '^TRM-[0-9a-f]{16}$'),
    term_text TEXT NOT NULL CHECK (length(btrim(term_text)) > 0),
    sort_order INTEGER NOT NULL CHECK (sort_order > 0),
    PRIMARY KEY(run_id,query_id,term_id),
    UNIQUE(run_id,query_id,sort_order),
    FOREIGN KEY(run_id,query_id)
        REFERENCES landscape_v4_search_queries(run_id,query_id) ON DELETE RESTRICT
);

CREATE OR REPLACE FUNCTION prevent_landscape_v4_query_plan_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'landscape v4 query plans are immutable';
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE table_name TEXT;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'landscape_v4_query_plans',
        'landscape_v4_search_queries',
        'landscape_v4_search_query_terms'
    ] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_%s_immutable ON %I', table_name, table_name);
        EXECUTE format(
            'CREATE TRIGGER trg_%s_immutable BEFORE UPDATE OR DELETE ON %I '
            'FOR EACH ROW EXECUTE FUNCTION prevent_landscape_v4_query_plan_mutation()',
            table_name,
            table_name
        );
    END LOOP;
END;
$$;

INSERT INTO aifpatent_schema_migrations(version)
VALUES ('088_landscape_v4_query_plans')
ON CONFLICT (version) DO NOTHING;

COMMIT;
