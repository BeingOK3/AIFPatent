BEGIN;

CREATE TABLE IF NOT EXISTS landscape_v4_direction_records (
    run_id TEXT NOT NULL,
    analysis_unit_id TEXT NOT NULL,
    taxonomy_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('AVAILABLE','UNRESOLVED')),
    evidence_sufficient BOOLEAN NOT NULL,
    technical_problem TEXT NOT NULL,
    solution_mechanism TEXT NOT NULL,
    technical_object TEXT NOT NULL,
    direction_summary TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    unresolved_reason TEXT,
    input_hash TEXT NOT NULL CHECK (input_hash ~ '^[0-9a-f]{64}$'),
    PRIMARY KEY(run_id,analysis_unit_id),
    FOREIGN KEY(run_id) REFERENCES landscape_v4_runs(run_id) ON DELETE RESTRICT,
    FOREIGN KEY(run_id,taxonomy_version)
        REFERENCES landscape_v4_runs(run_id,taxonomy_version) ON DELETE RESTRICT,
    CHECK ((status='AVAILABLE' AND unresolved_reason IS NULL)
        OR (status='UNRESOLVED' AND unresolved_reason IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS landscape_v4_direction_values (
    run_id TEXT NOT NULL,
    analysis_unit_id TEXT NOT NULL,
    value_type TEXT NOT NULL CHECK (value_type IN ('SCENARIO','KEYWORD','LEVEL1','EVIDENCE')),
    value_text TEXT NOT NULL,
    sort_order INTEGER NOT NULL CHECK (sort_order > 0),
    PRIMARY KEY(run_id,analysis_unit_id,value_type,value_text),
    UNIQUE(run_id,analysis_unit_id,value_type,sort_order),
    FOREIGN KEY(run_id,analysis_unit_id)
        REFERENCES landscape_v4_direction_records(run_id,analysis_unit_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS landscape_v4_classification_results (
    run_id TEXT NOT NULL,
    analysis_unit_id TEXT NOT NULL,
    taxonomy_version TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('EXACT_CATEGORY','NONE_OF_CANDIDATES','NEEDS_ALTERNATIVE_PARENT','UNRESOLVED')),
    terminal TEXT NOT NULL CHECK (terminal IN ('CLASSIFIED','OTHERS','UNRESOLVED')),
    primary_category_id TEXT,
    confidence DOUBLE PRECISION NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    review_round INTEGER NOT NULL CHECK (review_round BETWEEN 0 AND 1),
    unresolved_reason TEXT,
    input_hash TEXT NOT NULL CHECK (input_hash ~ '^[0-9a-f]{64}$'),
    PRIMARY KEY(run_id,analysis_unit_id),
    FOREIGN KEY(run_id,analysis_unit_id)
        REFERENCES landscape_v4_direction_records(run_id,analysis_unit_id) ON DELETE RESTRICT,
    FOREIGN KEY(run_id,taxonomy_version)
        REFERENCES landscape_v4_runs(run_id,taxonomy_version) ON DELETE RESTRICT,
    FOREIGN KEY(taxonomy_version,primary_category_id)
        REFERENCES landscape_taxonomy_categories(taxonomy_version,category_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS landscape_v4_classification_values (
    run_id TEXT NOT NULL,
    analysis_unit_id TEXT NOT NULL,
    value_type TEXT NOT NULL CHECK (value_type IN ('AUXILIARY_CATEGORY','EVIDENCE')),
    value_text TEXT NOT NULL,
    sort_order INTEGER NOT NULL CHECK (sort_order > 0),
    PRIMARY KEY(run_id,analysis_unit_id,value_type,value_text),
    UNIQUE(run_id,analysis_unit_id,value_type,sort_order),
    FOREIGN KEY(run_id,analysis_unit_id)
        REFERENCES landscape_v4_classification_results(run_id,analysis_unit_id) ON DELETE RESTRICT
);

CREATE OR REPLACE FUNCTION prevent_landscape_v4_semantic_result_mutation()
RETURNS trigger AS $$ BEGIN RAISE EXCEPTION 'landscape v4 semantic results are immutable'; END; $$ LANGUAGE plpgsql;

DO $$
DECLARE table_name TEXT;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'landscape_v4_direction_records','landscape_v4_direction_values',
        'landscape_v4_classification_results','landscape_v4_classification_values'
    ] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_%s_immutable ON %I',table_name,table_name);
        EXECUTE format('CREATE TRIGGER trg_%s_immutable BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION prevent_landscape_v4_semantic_result_mutation()',table_name,table_name);
    END LOOP;
END;
$$;

INSERT INTO aifpatent_schema_migrations(version)
VALUES ('094_landscape_v4_direction_classification')
ON CONFLICT (version) DO NOTHING;

COMMIT;
