BEGIN;

CREATE TABLE IF NOT EXISTS landscape_v4_others_manifests (
    run_id TEXT PRIMARY KEY,
    taxonomy_version TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    member_count INTEGER NOT NULL CHECK (member_count >= 0),
    cluster_count INTEGER NOT NULL CHECK (cluster_count >= 0),
    input_hash TEXT NOT NULL CHECK (input_hash ~ '^[0-9a-f]{64}$'),
    FOREIGN KEY(run_id, taxonomy_version)
        REFERENCES landscape_v4_runs(run_id, taxonomy_version) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS landscape_v4_others_clusters (
    run_id TEXT NOT NULL,
    cluster_id TEXT NOT NULL,
    taxonomy_version TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('CANDIDATE','SINGLETON','NOISE')),
    representative_analysis_unit_id TEXT NOT NULL,
    cohesion DOUBLE PRECISION NOT NULL CHECK (cohesion BETWEEN 0 AND 1),
    name TEXT NOT NULL,
    technical_problem TEXT NOT NULL,
    common_mechanism TEXT NOT NULL,
    direction_boundary TEXT NOT NULL,
    keywords_json JSONB NOT NULL,
    naming_source TEXT NOT NULL CHECK (naming_source IN ('DETERMINISTIC','MODEL')),
    input_hash TEXT NOT NULL CHECK (input_hash ~ '^[0-9a-f]{64}$'),
    PRIMARY KEY(run_id, cluster_id),
    CONSTRAINT landscape_v4_others_clusters_run_manifest_fkey FOREIGN KEY(run_id)
        REFERENCES landscape_v4_others_manifests(run_id) ON DELETE RESTRICT,
    FOREIGN KEY(run_id, representative_analysis_unit_id)
        REFERENCES landscape_v4_direction_records(run_id, analysis_unit_id) ON DELETE RESTRICT,
    CHECK (length(name) > 0)
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'landscape_v4_others_clusters_run_manifest_fkey'
    ) THEN
        ALTER TABLE landscape_v4_others_clusters
            ADD CONSTRAINT landscape_v4_others_clusters_run_manifest_fkey
            FOREIGN KEY(run_id) REFERENCES landscape_v4_others_manifests(run_id)
            ON DELETE RESTRICT;
    END IF;
END;
$$;

CREATE TABLE IF NOT EXISTS landscape_v4_others_members (
    run_id TEXT NOT NULL,
    cluster_id TEXT NOT NULL,
    analysis_unit_id TEXT NOT NULL,
    sort_order INTEGER NOT NULL CHECK (sort_order > 0),
    PRIMARY KEY(run_id, cluster_id, analysis_unit_id),
    UNIQUE(run_id, analysis_unit_id),
    FOREIGN KEY(run_id, cluster_id)
        REFERENCES landscape_v4_others_clusters(run_id, cluster_id) ON DELETE RESTRICT,
    FOREIGN KEY(run_id, analysis_unit_id)
        REFERENCES landscape_v4_direction_records(run_id, analysis_unit_id) ON DELETE RESTRICT,
    CHECK (sort_order > 0)
);

CREATE OR REPLACE FUNCTION prevent_landscape_v4_others_mutation()
RETURNS trigger AS $$ BEGIN RAISE EXCEPTION 'landscape v4 Others results are immutable'; END; $$ LANGUAGE plpgsql;

DO $$
BEGIN
    DROP TRIGGER IF EXISTS trg_landscape_v4_others_manifests_immutable ON landscape_v4_others_manifests;
    CREATE TRIGGER trg_landscape_v4_others_manifests_immutable
        BEFORE UPDATE OR DELETE ON landscape_v4_others_manifests
        FOR EACH ROW EXECUTE FUNCTION prevent_landscape_v4_others_mutation();
    DROP TRIGGER IF EXISTS trg_landscape_v4_others_clusters_immutable ON landscape_v4_others_clusters;
    CREATE TRIGGER trg_landscape_v4_others_clusters_immutable
        BEFORE UPDATE OR DELETE ON landscape_v4_others_clusters
        FOR EACH ROW EXECUTE FUNCTION prevent_landscape_v4_others_mutation();
    DROP TRIGGER IF EXISTS trg_landscape_v4_others_members_immutable ON landscape_v4_others_members;
    CREATE TRIGGER trg_landscape_v4_others_members_immutable
        BEFORE UPDATE OR DELETE ON landscape_v4_others_members
        FOR EACH ROW EXECUTE FUNCTION prevent_landscape_v4_others_mutation();
END;
$$;

INSERT INTO aifpatent_schema_migrations(version)
VALUES ('095_landscape_v4_others')
ON CONFLICT (version) DO NOTHING;

COMMIT;
