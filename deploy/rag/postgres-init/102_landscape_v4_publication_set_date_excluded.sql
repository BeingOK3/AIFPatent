BEGIN;

ALTER TABLE landscape_v4_publication_sets
    ADD COLUMN IF NOT EXISTS date_excluded_count INTEGER NOT NULL DEFAULT 0
    CHECK (date_excluded_count >= 0);

COMMIT;
