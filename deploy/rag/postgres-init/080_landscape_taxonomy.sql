-- Immutable Landscape v4 taxonomy versions compiled from classify.md.

BEGIN;

CREATE TABLE IF NOT EXISTS landscape_taxonomy_versions (
    taxonomy_version TEXT PRIMARY KEY CHECK (taxonomy_version ~ '^TAX-[0-9a-f]{16}$'),
    schema_version TEXT NOT NULL,
    taxonomy_hash TEXT NOT NULL UNIQUE CHECK (taxonomy_hash ~ '^[0-9a-f]{64}$'),
    source_hash TEXT NOT NULL CHECK (source_hash ~ '^[0-9a-f]{64}$'),
    source_row_count INTEGER NOT NULL CHECK (source_row_count > 0),
    node_count INTEGER NOT NULL CHECK (node_count > 0),
    leaf_count INTEGER NOT NULL CHECK (leaf_count > 0),
    artifact_json JSONB NOT NULL,
    created_at BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS landscape_taxonomy_categories (
    taxonomy_version TEXT NOT NULL
        REFERENCES landscape_taxonomy_versions(taxonomy_version) ON DELETE RESTRICT,
    category_id TEXT NOT NULL CHECK (category_id ~ '^CAT-[0-9a-f]{16}$'),
    parent_id TEXT,
    level INTEGER NOT NULL CHECK (level BETWEEN 1 AND 3),
    name TEXT NOT NULL CHECK (length(btrim(name)) > 0),
    path_json JSONB NOT NULL,
    is_leaf BOOLEAN NOT NULL,
    sort_order INTEGER NOT NULL CHECK (sort_order > 0),
    content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    created_at BIGINT NOT NULL,
    PRIMARY KEY(taxonomy_version, category_id),
    UNIQUE(taxonomy_version, sort_order),
    FOREIGN KEY(taxonomy_version, parent_id)
        REFERENCES landscape_taxonomy_categories(taxonomy_version, category_id)
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_landscape_taxonomy_categories_parent
    ON landscape_taxonomy_categories(taxonomy_version, parent_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_landscape_taxonomy_categories_leaf
    ON landscape_taxonomy_categories(taxonomy_version, is_leaf, sort_order);

CREATE OR REPLACE FUNCTION prevent_landscape_taxonomy_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'Landscape taxonomy versions and categories are immutable';
END;
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_landscape_taxonomy_versions_immutable'
    ) THEN
        CREATE TRIGGER trg_landscape_taxonomy_versions_immutable
        BEFORE UPDATE OR DELETE ON landscape_taxonomy_versions
        FOR EACH ROW EXECUTE FUNCTION prevent_landscape_taxonomy_mutation();
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_landscape_taxonomy_categories_immutable'
    ) THEN
        CREATE TRIGGER trg_landscape_taxonomy_categories_immutable
        BEFORE UPDATE OR DELETE ON landscape_taxonomy_categories
        FOR EACH ROW EXECUTE FUNCTION prevent_landscape_taxonomy_mutation();
    END IF;
END;
$$;

INSERT INTO aifpatent_schema_migrations(version)
VALUES ('080_landscape_taxonomy')
ON CONFLICT (version) DO NOTHING;

COMMIT;
