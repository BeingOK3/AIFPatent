-- Durable, resumable detail-fetch state for every canonical Landscape candidate.

BEGIN;

CREATE TABLE IF NOT EXISTS landscape_document_fetches (
    run_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    publication_number TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('FETCHED','FAILED')),
    document_json JSONB,
    content_hash TEXT,
    attempt_count INTEGER NOT NULL CHECK (attempt_count >= 1),
    error_code TEXT,
    error_message TEXT,
    updated_at BIGINT NOT NULL,
    PRIMARY KEY(run_id, document_id),
    UNIQUE(run_id, publication_number),
    FOREIGN KEY(run_id, document_id)
        REFERENCES landscape_candidates(run_id, document_id) ON DELETE CASCADE,
    CHECK (
        (status = 'FETCHED' AND document_json IS NOT NULL AND content_hash IS NOT NULL
         AND error_code IS NULL AND error_message IS NULL)
        OR
        (status = 'FAILED' AND document_json IS NULL AND content_hash IS NULL
         AND error_code IS NOT NULL)
    )
);

INSERT INTO aifpatent_schema_migrations(version)
VALUES ('072_landscape_document_fetches')
ON CONFLICT (version) DO NOTHING;

COMMIT;
