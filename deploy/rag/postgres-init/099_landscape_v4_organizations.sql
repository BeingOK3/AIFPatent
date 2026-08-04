BEGIN;

CREATE TABLE IF NOT EXISTS landscape_v4_organization_manifests (
    run_id TEXT PRIMARY KEY,
    policy_version TEXT NOT NULL,
    organization_count INTEGER NOT NULL CHECK (organization_count >= 0),
    assignment_count INTEGER NOT NULL CHECK (assignment_count >= 0),
    FOREIGN KEY(run_id) REFERENCES landscape_v4_publication_sets(run_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS landscape_v4_organizations (
    run_id TEXT NOT NULL,
    organization_id TEXT NOT NULL CHECK (organization_id ~ '^ORG-([0-9a-f]{16}|UNKNOWN)$'),
    display_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    organization_type TEXT NOT NULL CHECK (organization_type IN (
        'COMPANY','ACADEMIC_RESEARCH','OTHER','UNKNOWN'
    )),
    source_profile_id TEXT,
    sort_order INTEGER NOT NULL CHECK (sort_order > 0),
    PRIMARY KEY(run_id,organization_id),
    UNIQUE(run_id,sort_order),
    FOREIGN KEY(run_id) REFERENCES landscape_v4_organization_manifests(run_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS landscape_v4_organization_names (
    run_id TEXT NOT NULL,
    organization_id TEXT NOT NULL,
    observed_name TEXT NOT NULL,
    sort_order INTEGER NOT NULL CHECK (sort_order > 0),
    PRIMARY KEY(run_id,organization_id,sort_order),
    UNIQUE(run_id,organization_id,observed_name),
    FOREIGN KEY(run_id,organization_id)
        REFERENCES landscape_v4_organizations(run_id,organization_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS landscape_v4_publication_organizations (
    run_id TEXT NOT NULL,
    publication_id TEXT NOT NULL,
    primary_organization_id TEXT NOT NULL,
    sort_order INTEGER NOT NULL CHECK (sort_order > 0),
    PRIMARY KEY(run_id,publication_id),
    UNIQUE(run_id,sort_order),
    FOREIGN KEY(run_id,publication_id)
        REFERENCES landscape_v4_publications(run_id,publication_id) ON DELETE RESTRICT,
    FOREIGN KEY(run_id,primary_organization_id)
        REFERENCES landscape_v4_organizations(run_id,organization_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS landscape_v4_publication_co_organizations (
    run_id TEXT NOT NULL,
    publication_id TEXT NOT NULL,
    organization_id TEXT NOT NULL,
    sort_order INTEGER NOT NULL CHECK (sort_order > 0),
    PRIMARY KEY(run_id,publication_id,organization_id),
    UNIQUE(run_id,publication_id,sort_order),
    FOREIGN KEY(run_id,publication_id)
        REFERENCES landscape_v4_publication_organizations(run_id,publication_id) ON DELETE RESTRICT,
    FOREIGN KEY(run_id,organization_id)
        REFERENCES landscape_v4_organizations(run_id,organization_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS landscape_v4_publication_applicants (
    run_id TEXT NOT NULL,
    publication_id TEXT NOT NULL,
    observed_name TEXT NOT NULL,
    is_unconfirmed BOOLEAN NOT NULL,
    sort_order INTEGER NOT NULL CHECK (sort_order > 0),
    PRIMARY KEY(run_id,publication_id,sort_order),
    UNIQUE(run_id,publication_id,observed_name),
    FOREIGN KEY(run_id,publication_id)
        REFERENCES landscape_v4_publication_organizations(run_id,publication_id) ON DELETE RESTRICT
);

CREATE OR REPLACE FUNCTION prevent_landscape_v4_organization_mutation()
RETURNS trigger AS $$ BEGIN RAISE EXCEPTION 'landscape v4 organization results are immutable'; END; $$ LANGUAGE plpgsql;

DO $$
DECLARE table_name TEXT;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'landscape_v4_organization_manifests','landscape_v4_organizations',
        'landscape_v4_organization_names','landscape_v4_publication_organizations',
        'landscape_v4_publication_co_organizations','landscape_v4_publication_applicants'
    ] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_%s_immutable ON %I',table_name,table_name);
        EXECUTE format('CREATE TRIGGER trg_%s_immutable BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION prevent_landscape_v4_organization_mutation()',table_name,table_name);
    END LOOP;
END;
$$;

INSERT INTO aifpatent_schema_migrations(version)
VALUES ('099_landscape_v4_organizations')
ON CONFLICT (version) DO NOTHING;

COMMIT;
