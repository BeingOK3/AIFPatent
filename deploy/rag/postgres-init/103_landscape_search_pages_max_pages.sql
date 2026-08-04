-- 检索页停止原因允许 MAX_PAGES（诚实截断标记）。
BEGIN;

ALTER TABLE landscape_v4_search_pages
    DROP CONSTRAINT landscape_v4_search_pages_stop_reason_check;

ALTER TABLE landscape_v4_search_pages
    ADD CONSTRAINT landscape_v4_search_pages_stop_reason_check
    CHECK (stop_reason = ANY (ARRAY[
        'MORE_AVAILABLE', 'QUERY_EXHAUSTED', 'PROVIDER_HARD_LIMIT', 'MAX_PAGES'
    ]));

INSERT INTO aifpatent_schema_migrations(version)
VALUES ('103_landscape_search_pages_max_pages')
ON CONFLICT (version) DO NOTHING;

COMMIT;
