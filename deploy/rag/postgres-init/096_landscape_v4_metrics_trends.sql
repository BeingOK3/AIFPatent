BEGIN;

CREATE TABLE IF NOT EXISTS landscape_v4_metric_manifests (
    run_id TEXT PRIMARY KEY,
    policy_version TEXT NOT NULL,
    publication_start DATE NOT NULL,
    publication_end DATE NOT NULL,
    organization_counting_mode TEXT NOT NULL CHECK (organization_counting_mode IN ('PRIMARY','ALL_KNOWN')),
    analysis_unit_time_policy TEXT NOT NULL,
    analysis_unit_count INTEGER NOT NULL CHECK (analysis_unit_count >= 0),
    publication_count INTEGER NOT NULL CHECK (publication_count >= 0),
    excluded_publication_count INTEGER NOT NULL CHECK (excluded_publication_count >= 0),
    cube_hash TEXT NOT NULL CHECK (cube_hash ~ '^[0-9a-f]{64}$'),
    FOREIGN KEY(run_id) REFERENCES landscape_v4_runs(run_id) ON DELETE RESTRICT,
    CHECK (publication_end >= publication_start)
);

CREATE TABLE IF NOT EXISTS landscape_v4_metric_buckets (
    run_id TEXT NOT NULL,
    bucket_id TEXT NOT NULL,
    sort_order INTEGER NOT NULL CHECK (sort_order > 0),
    label TEXT NOT NULL,
    bucket_start DATE NOT NULL,
    bucket_end DATE NOT NULL,
    granularity TEXT NOT NULL CHECK (granularity IN ('MONTH','QUARTER','YEAR')),
    PRIMARY KEY(run_id,bucket_id),
    UNIQUE(run_id,sort_order),
    FOREIGN KEY(run_id) REFERENCES landscape_v4_metric_manifests(run_id) ON DELETE RESTRICT,
    CHECK (bucket_end >= bucket_start)
);

CREATE TABLE IF NOT EXISTS landscape_v4_metric_cells (
    run_id TEXT NOT NULL,
    direction_id TEXT NOT NULL,
    organization_id TEXT NOT NULL,
    bucket_id TEXT NOT NULL,
    analysis_unit_count INTEGER NOT NULL CHECK (analysis_unit_count >= 0),
    publication_count INTEGER NOT NULL CHECK (publication_count >= 0),
    direction_share DOUBLE PRECISION NOT NULL CHECK (direction_share BETWEEN 0 AND 1),
    PRIMARY KEY(run_id,direction_id,organization_id,bucket_id),
    FOREIGN KEY(run_id,bucket_id)
        REFERENCES landscape_v4_metric_buckets(run_id,bucket_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS landscape_v4_metric_cell_values (
    run_id TEXT NOT NULL,
    direction_id TEXT NOT NULL,
    organization_id TEXT NOT NULL,
    bucket_id TEXT NOT NULL,
    value_type TEXT NOT NULL CHECK (value_type IN ('ANALYSIS_UNIT','PUBLICATION')),
    value_id TEXT NOT NULL,
    sort_order INTEGER NOT NULL CHECK (sort_order > 0),
    PRIMARY KEY(run_id,direction_id,organization_id,bucket_id,value_type,value_id),
    UNIQUE(run_id,direction_id,organization_id,bucket_id,value_type,sort_order),
    FOREIGN KEY(run_id,direction_id,organization_id,bucket_id)
        REFERENCES landscape_v4_metric_cells(run_id,direction_id,organization_id,bucket_id)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS landscape_v4_trend_manifests (
    run_id TEXT PRIMARY KEY,
    policy_version TEXT NOT NULL,
    candidate_count INTEGER NOT NULL CHECK (candidate_count >= 0),
    input_hash TEXT NOT NULL CHECK (input_hash ~ '^[0-9a-f]{64}$'),
    FOREIGN KEY(run_id) REFERENCES landscape_v4_metric_manifests(run_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS landscape_v4_trend_candidates (
    run_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    direction_id TEXT NOT NULL,
    change_type TEXT NOT NULL CHECK (change_type IN ('NEW','SUSTAINED_ACTIVE','STRENGTHENING','WEAKENING','CURRENT_LAYOUT')),
    conclusion_strength TEXT NOT NULL CHECK (conclusion_strength IN ('OBSERVATION','STRONG')),
    PRIMARY KEY(run_id,candidate_id),
    FOREIGN KEY(run_id) REFERENCES landscape_v4_trend_manifests(run_id) ON DELETE RESTRICT,
    CHECK (conclusion_strength='STRONG' OR change_type='CURRENT_LAYOUT')
);

CREATE TABLE IF NOT EXISTS landscape_v4_trend_bucket_metrics (
    run_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    bucket_id TEXT NOT NULL,
    sort_order INTEGER NOT NULL CHECK (sort_order > 0),
    analysis_unit_count INTEGER NOT NULL CHECK (analysis_unit_count >= 0),
    publication_count INTEGER NOT NULL CHECK (publication_count >= 0),
    PRIMARY KEY(run_id,candidate_id,bucket_id),
    UNIQUE(run_id,candidate_id,sort_order),
    FOREIGN KEY(run_id,candidate_id)
        REFERENCES landscape_v4_trend_candidates(run_id,candidate_id) ON DELETE RESTRICT,
    FOREIGN KEY(run_id,bucket_id)
        REFERENCES landscape_v4_metric_buckets(run_id,bucket_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS landscape_v4_trend_values (
    run_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    value_type TEXT NOT NULL CHECK (value_type IN ('ORGANIZATION','ANALYSIS_UNIT','REPRESENTATIVE','EVIDENCE','LIMITATION')),
    value_id TEXT NOT NULL,
    sort_order INTEGER NOT NULL CHECK (sort_order > 0),
    PRIMARY KEY(run_id,candidate_id,value_type,value_id),
    UNIQUE(run_id,candidate_id,value_type,sort_order),
    FOREIGN KEY(run_id,candidate_id)
        REFERENCES landscape_v4_trend_candidates(run_id,candidate_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS landscape_v4_representative_manifests (
    run_id TEXT PRIMARY KEY,
    representative_count INTEGER NOT NULL CHECK (representative_count >= 0),
    input_hash TEXT NOT NULL CHECK (input_hash ~ '^[0-9a-f]{64}$'),
    FOREIGN KEY(run_id) REFERENCES landscape_v4_metric_manifests(run_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS landscape_v4_representatives (
    run_id TEXT NOT NULL,
    representative_id TEXT NOT NULL,
    selection_rank INTEGER NOT NULL CHECK (selection_rank BETWEEN 1 AND 10),
    direction_id TEXT NOT NULL,
    analysis_unit_id TEXT NOT NULL,
    publication_id TEXT NOT NULL,
    title TEXT NOT NULL,
    publication_number TEXT NOT NULL,
    publication_date DATE NOT NULL,
    organization_ids_json JSONB NOT NULL,
    classification_path_json JSONB NOT NULL,
    selection_reasons_json JSONB NOT NULL,
    patent_url TEXT,
    link_status TEXT NOT NULL CHECK (link_status IN ('AVAILABLE','UNAVAILABLE')),
    PRIMARY KEY(run_id,representative_id),
    UNIQUE(run_id,direction_id,analysis_unit_id),
    UNIQUE(run_id,direction_id,selection_rank),
    FOREIGN KEY(run_id) REFERENCES landscape_v4_representative_manifests(run_id) ON DELETE RESTRICT,
    FOREIGN KEY(run_id,analysis_unit_id)
        REFERENCES landscape_v4_direction_records(run_id,analysis_unit_id) ON DELETE RESTRICT,
    CHECK ((link_status='AVAILABLE' AND patent_url IS NOT NULL)
        OR (link_status='UNAVAILABLE' AND patent_url IS NULL))
);

CREATE OR REPLACE FUNCTION prevent_landscape_v4_analytics_mutation()
RETURNS trigger AS $$ BEGIN RAISE EXCEPTION 'landscape v4 analytics results are immutable'; END; $$ LANGUAGE plpgsql;

DO $$
DECLARE table_name TEXT;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'landscape_v4_metric_manifests','landscape_v4_metric_buckets',
        'landscape_v4_metric_cells','landscape_v4_metric_cell_values',
        'landscape_v4_trend_manifests','landscape_v4_trend_candidates',
        'landscape_v4_trend_bucket_metrics','landscape_v4_trend_values',
        'landscape_v4_representative_manifests','landscape_v4_representatives'
    ] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_%s_immutable ON %I',table_name,table_name);
        EXECUTE format('CREATE TRIGGER trg_%s_immutable BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION prevent_landscape_v4_analytics_mutation()',table_name,table_name);
    END LOOP;
END;
$$;

INSERT INTO aifpatent_schema_migrations(version)
VALUES ('096_landscape_v4_metrics_trends')
ON CONFLICT (version) DO NOTHING;

COMMIT;
