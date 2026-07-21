BEGIN;

ALTER TABLE report_retrieval_hits
    ADD COLUMN IF NOT EXISTS query_id TEXT;
ALTER TABLE report_retrieval_hits
    ADD COLUMN IF NOT EXISTS lexical_score DOUBLE PRECISION;
ALTER TABLE report_retrieval_hits
    ADD COLUMN IF NOT EXISTS match_kind TEXT;

CREATE TABLE IF NOT EXISTS report_model_citations (
    run_id TEXT NOT NULL REFERENCES idea_runs(run_id) ON DELETE CASCADE,
    document_id TEXT NOT NULL REFERENCES patent_documents(document_id) ON DELETE RESTRICT,
    feature_id TEXT NOT NULL REFERENCES idea_features(feature_id) ON DELETE CASCADE,
    context_id TEXT NOT NULL REFERENCES model_context_manifests(context_id) ON DELETE RESTRICT,
    alias TEXT NOT NULL CHECK (alias ~ '^C[1-9][0-9]*$'),
    chunk_id TEXT NOT NULL REFERENCES patent_chunks(chunk_id) ON DELETE RESTRICT,
    created_at BIGINT NOT NULL,
    PRIMARY KEY(run_id, document_id, feature_id, context_id, alias, chunk_id)
);
CREATE INDEX IF NOT EXISTS idx_report_model_citations_run
    ON report_model_citations(run_id, feature_id, document_id);

INSERT INTO aifpatent_schema_migrations(version)
VALUES ('040_report_citation_schema')
ON CONFLICT (version) DO NOTHING;

COMMIT;
