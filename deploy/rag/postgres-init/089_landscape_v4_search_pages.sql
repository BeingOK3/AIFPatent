BEGIN;

CREATE TABLE IF NOT EXISTS landscape_v4_search_pages (
    run_id TEXT NOT NULL,
    query_id TEXT NOT NULL,
    page_number INTEGER NOT NULL CHECK (page_number > 0),
    cursor_in TEXT,
    next_cursor TEXT,
    reported_total_results INTEGER CHECK (reported_total_results >= 0),
    reported_total_pages INTEGER CHECK (reported_total_pages >= 0),
    provider_request_id TEXT NOT NULL,
    stop_reason TEXT NOT NULL CHECK (stop_reason IN (
        'MORE_AVAILABLE','QUERY_EXHAUSTED','PROVIDER_HARD_LIMIT'
    )),
    hits_json JSONB NOT NULL,
    created_at BIGINT NOT NULL CHECK (created_at >= 0),
    PRIMARY KEY(run_id,query_id,page_number),
    FOREIGN KEY(run_id,query_id)
        REFERENCES landscape_v4_search_queries(run_id,query_id) ON DELETE RESTRICT,
    CHECK ((next_cursor IS NULL) OR stop_reason = 'MORE_AVAILABLE'),
    CHECK ((next_cursor IS NOT NULL) OR stop_reason <> 'MORE_AVAILABLE')
);

CREATE OR REPLACE FUNCTION prevent_landscape_v4_search_page_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'landscape v4 search pages are immutable';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_landscape_v4_search_pages_immutable
    ON landscape_v4_search_pages;
CREATE TRIGGER trg_landscape_v4_search_pages_immutable
BEFORE UPDATE OR DELETE ON landscape_v4_search_pages
FOR EACH ROW EXECUTE FUNCTION prevent_landscape_v4_search_page_mutation();

INSERT INTO aifpatent_schema_migrations(version)
VALUES ('089_landscape_v4_search_pages')
ON CONFLICT (version) DO NOTHING;

COMMIT;
