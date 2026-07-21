BEGIN;

CREATE TABLE IF NOT EXISTS report_retrieval_queries (
    query_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES idea_runs(run_id) ON DELETE CASCADE,
    document_id TEXT NOT NULL REFERENCES patent_documents(document_id) ON DELETE RESTRICT,
    version_id TEXT NOT NULL REFERENCES patent_document_versions(version_id) ON DELETE RESTRICT,
    feature_id TEXT NOT NULL REFERENCES idea_features(feature_id) ON DELETE CASCADE,
    publication_number TEXT NOT NULL,
    query_text TEXT NOT NULL,
    hit_count INTEGER NOT NULL CHECK (hit_count >= 0),
    retriever_version TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    UNIQUE(run_id, version_id, feature_id, retriever_version)
);
CREATE INDEX IF NOT EXISTS idx_report_retrieval_queries_run
    ON report_retrieval_queries(run_id, feature_id, version_id);

INSERT INTO aifpatent_schema_migrations(version)
VALUES ('035_report_retrieval_schema')
ON CONFLICT (version) DO NOTHING;

COMMIT;
