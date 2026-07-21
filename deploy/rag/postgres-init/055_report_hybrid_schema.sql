BEGIN;

ALTER TABLE report_retrieval_hits
    DROP CONSTRAINT IF EXISTS report_retrieval_hits_selection_reason_check;
ALTER TABLE report_retrieval_hits
    ADD CONSTRAINT report_retrieval_hits_selection_reason_check
    CHECK (selection_reason IN (
        'forced_abstract', 'forced_claim', 'lexical', 'vector', 'hybrid', 'rerank'
    ));

ALTER TABLE report_retrieval_hits
    ADD COLUMN IF NOT EXISTS final_rank INTEGER
        CHECK (final_rank IS NULL OR final_rank > 0),
    ADD COLUMN IF NOT EXISTS section_weight REAL
        CHECK (section_weight IS NULL OR section_weight = section_weight),
    ADD COLUMN IF NOT EXISTS final_score REAL
        CHECK (final_score IS NULL OR final_score = final_score),
    ADD COLUMN IF NOT EXISTS query_sources_json JSONB NOT NULL DEFAULT '[]'::jsonb;

INSERT INTO aifpatent_schema_migrations(version)
VALUES ('055_report_hybrid_schema')
ON CONFLICT (version) DO NOTHING;

COMMIT;
