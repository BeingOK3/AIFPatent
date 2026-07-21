BEGIN;

ALTER TABLE patent_chunks
    ADD COLUMN IF NOT EXISTS search_terms TEXT NOT NULL DEFAULT '';
ALTER TABLE patent_chunks
    ADD COLUMN IF NOT EXISTS search_text TEXT GENERATED ALWAYS AS (
        lower(publication_number || ' ' || section_type || ' ' || section_label || ' ' || text)
    ) STORED;
ALTER TABLE patent_chunks
    ADD COLUMN IF NOT EXISTS search_tsv TSVECTOR GENERATED ALWAYS AS (
        setweight(to_tsvector('simple'::regconfig, search_terms), 'A') ||
        setweight(to_tsvector('english'::regconfig, text), 'B')
    ) STORED;

CREATE INDEX IF NOT EXISTS idx_patent_chunks_search_tsv
    ON patent_chunks USING GIN (search_tsv);
CREATE INDEX IF NOT EXISTS idx_patent_chunks_search_trgm
    ON patent_chunks USING GIN (search_text gin_trgm_ops);

INSERT INTO aifpatent_schema_migrations(version)
VALUES ('030_lexical_schema')
ON CONFLICT (version) DO NOTHING;

COMMIT;
