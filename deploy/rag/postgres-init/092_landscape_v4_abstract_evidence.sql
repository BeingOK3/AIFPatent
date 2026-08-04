BEGIN;

CREATE TABLE IF NOT EXISTS landscape_v4_abstract_evidence (
    run_id TEXT NOT NULL,
    publication_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('AVAILABLE','MISSING','INVALID','PROVIDER_FAILED')),
    title TEXT NOT NULL,
    abstract_text TEXT NOT NULL,
    normalized_abstract TEXT NOT NULL,
    content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    provider TEXT NOT NULL,
    unresolved_reason TEXT,
    PRIMARY KEY(run_id,publication_id),
    FOREIGN KEY(run_id,publication_id)
        REFERENCES landscape_v4_publications(run_id,publication_id) ON DELETE RESTRICT,
    CHECK ((status = 'AVAILABLE' AND unresolved_reason IS NULL)
        OR (status <> 'AVAILABLE' AND unresolved_reason IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS landscape_v4_abstract_sentences (
    run_id TEXT NOT NULL,
    publication_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    sentence_order INTEGER NOT NULL CHECK (sentence_order > 0),
    sentence_text TEXT NOT NULL CHECK (length(btrim(sentence_text)) > 0),
    PRIMARY KEY(run_id,publication_id,evidence_id),
    UNIQUE(run_id,publication_id,sentence_order),
    FOREIGN KEY(run_id,publication_id)
        REFERENCES landscape_v4_abstract_evidence(run_id,publication_id) ON DELETE RESTRICT
);

CREATE OR REPLACE FUNCTION prevent_landscape_v4_abstract_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'landscape v4 abstract evidence is immutable';
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE table_name TEXT;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'landscape_v4_abstract_evidence','landscape_v4_abstract_sentences'
    ] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_%s_immutable ON %I', table_name, table_name);
        EXECUTE format(
            'CREATE TRIGGER trg_%s_immutable BEFORE UPDATE OR DELETE ON %I '
            'FOR EACH ROW EXECUTE FUNCTION prevent_landscape_v4_abstract_mutation()',
            table_name, table_name
        );
    END LOOP;
END;
$$;

INSERT INTO aifpatent_schema_migrations(version)
VALUES ('092_landscape_v4_abstract_evidence')
ON CONFLICT (version) DO NOTHING;

COMMIT;
