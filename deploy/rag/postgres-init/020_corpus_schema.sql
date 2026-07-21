BEGIN;

-- Durable corpus records are separate from the mutable patent_documents cache.
CREATE TABLE IF NOT EXISTS corpus_blobs (
    blob_hash TEXT PRIMARY KEY CHECK (blob_hash ~ '^[0-9a-f]{64}$'),
    encoding TEXT NOT NULL CHECK (encoding IN ('identity', 'gzip', 'zstd')),
    object_key TEXT NOT NULL UNIQUE,
    uncompressed_bytes BIGINT NOT NULL CHECK (uncompressed_bytes >= 0),
    compressed_bytes BIGINT NOT NULL CHECK (compressed_bytes >= 0),
    content_type TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('READY', 'CORRUPT', 'MISSING')),
    created_at BIGINT NOT NULL,
    verified_at BIGINT
);

CREATE TABLE IF NOT EXISTS patent_document_versions (
    version_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES patent_documents(document_id) ON DELETE RESTRICT,
    language TEXT NOT NULL,
    normalized_content_hash TEXT NOT NULL CHECK (normalized_content_hash ~ '^[0-9a-f]{64}$'),
    normalized_blob_hash TEXT NOT NULL REFERENCES corpus_blobs(blob_hash) ON DELETE RESTRICT,
    parser_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('READY', 'CORRUPT', 'QUARANTINED')),
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at BIGINT NOT NULL,
    UNIQUE(document_id, language, normalized_content_hash)
);
CREATE INDEX IF NOT EXISTS idx_patent_versions_document ON patent_document_versions(document_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_patent_versions_state ON patent_document_versions(state, created_at);

CREATE TABLE IF NOT EXISTS patent_version_sources (
    source_id TEXT PRIMARY KEY,
    version_id TEXT NOT NULL REFERENCES patent_document_versions(version_id) ON DELETE RESTRICT,
    provider TEXT NOT NULL,
    source_url TEXT NOT NULL,
    retrieved_at BIGINT NOT NULL,
    raw_response_hash TEXT CHECK (raw_response_hash IS NULL OR raw_response_hash ~ '^[0-9a-f]{64}$'),
    parser_version TEXT NOT NULL,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_version_sources_version ON patent_version_sources(version_id, retrieved_at DESC);

CREATE TABLE IF NOT EXISTS run_document_versions (
    run_id TEXT NOT NULL REFERENCES idea_runs(run_id) ON DELETE CASCADE,
    document_id TEXT NOT NULL REFERENCES patent_documents(document_id) ON DELETE RESTRICT,
    version_id TEXT NOT NULL REFERENCES patent_document_versions(version_id) ON DELETE RESTRICT,
    deep_reviewed BOOLEAN NOT NULL DEFAULT FALSE,
    corpus_availability TEXT NOT NULL CHECK (corpus_availability IN ('READY', 'REHYDRATABLE', 'UNAVAILABLE', 'CORRUPT')),
    linked_at BIGINT NOT NULL,
    PRIMARY KEY(run_id, document_id)
);
CREATE INDEX IF NOT EXISTS idx_run_document_versions_version ON run_document_versions(version_id, run_id);

CREATE TABLE IF NOT EXISTS patent_chunks (
    chunk_id TEXT PRIMARY KEY,
    version_id TEXT NOT NULL REFERENCES patent_document_versions(version_id) ON DELETE RESTRICT,
    publication_number TEXT NOT NULL,
    section_type TEXT NOT NULL,
    section_label TEXT NOT NULL,
    claim_number TEXT,
    claim_kind TEXT CHECK (claim_kind IS NULL OR claim_kind IN ('independent', 'dependent', 'unknown')),
    parent_claims_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    start_offset INTEGER,
    end_offset INTEGER,
    text TEXT NOT NULL,
    text_hash TEXT NOT NULL CHECK (text_hash ~ '^[0-9a-f]{64}$'),
    token_count INTEGER NOT NULL CHECK (token_count >= 0),
    chunker_version TEXT NOT NULL,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at BIGINT NOT NULL,
    CHECK (start_offset IS NULL OR start_offset >= 0),
    CHECK (end_offset IS NULL OR end_offset >= 0),
    CHECK (start_offset IS NULL OR end_offset IS NULL OR end_offset >= start_offset),
    UNIQUE(version_id, section_type, section_label, start_offset, end_offset, chunker_version)
);
CREATE INDEX IF NOT EXISTS idx_patent_chunks_version ON patent_chunks(version_id, section_type, section_label);
CREATE INDEX IF NOT EXISTS idx_patent_chunks_publication ON patent_chunks(publication_number);

-- The vector type is dimension-agnostic here; active profiles enforce dimensions
-- at the adapter/index boundary until a profile-specific index is provisioned.
CREATE TABLE IF NOT EXISTS embedding_profiles (
    profile_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    dimensions INTEGER NOT NULL CHECK (dimensions > 0),
    normalization TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('BUILDING', 'ACTIVE', 'RETIRED', 'FAILED')),
    created_at BIGINT NOT NULL,
    activated_at BIGINT,
    UNIQUE(provider, model, dimensions, normalization)
);

CREATE TABLE IF NOT EXISTS embedding_vectors (
    embedding_id TEXT PRIMARY KEY,
    text_hash TEXT NOT NULL CHECK (text_hash ~ '^[0-9a-f]{64}$'),
    profile_id TEXT NOT NULL REFERENCES embedding_profiles(profile_id) ON DELETE RESTRICT,
    embedding VECTOR NOT NULL,
    vector_norm REAL NOT NULL CHECK (vector_norm > 0 AND vector_norm = vector_norm),
    created_at BIGINT NOT NULL,
    UNIQUE(text_hash, profile_id)
);

CREATE TABLE IF NOT EXISTS chunk_embeddings (
    chunk_id TEXT NOT NULL REFERENCES patent_chunks(chunk_id) ON DELETE RESTRICT,
    embedding_id TEXT NOT NULL REFERENCES embedding_vectors(embedding_id) ON DELETE RESTRICT,
    PRIMARY KEY(chunk_id, embedding_id)
);

CREATE TABLE IF NOT EXISTS report_retrieval_hits (
    run_id TEXT NOT NULL REFERENCES idea_runs(run_id) ON DELETE CASCADE,
    version_id TEXT NOT NULL REFERENCES patent_document_versions(version_id) ON DELETE RESTRICT,
    feature_id TEXT NOT NULL REFERENCES idea_features(feature_id) ON DELETE CASCADE,
    chunk_id TEXT NOT NULL REFERENCES patent_chunks(chunk_id) ON DELETE RESTRICT,
    selection_reason TEXT NOT NULL CHECK (selection_reason IN ('forced_abstract', 'forced_claim', 'lexical', 'vector', 'rerank')),
    lexical_rank INTEGER CHECK (lexical_rank IS NULL OR lexical_rank > 0),
    vector_rank INTEGER CHECK (vector_rank IS NULL OR vector_rank > 0),
    rrf_score REAL CHECK (rrf_score IS NULL OR rrf_score = rrf_score),
    rerank_score REAL CHECK (rerank_score IS NULL OR rerank_score = rerank_score),
    selected_for_context BOOLEAN NOT NULL,
    retriever_version TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    PRIMARY KEY(run_id, version_id, feature_id, chunk_id, selection_reason)
);
CREATE INDEX IF NOT EXISTS idx_report_retrieval_hits_run_feature ON report_retrieval_hits(run_id, feature_id, selected_for_context);

INSERT INTO aifpatent_schema_migrations(version)
VALUES ('020_corpus_schema')
ON CONFLICT (version) DO NOTHING;

COMMIT;
