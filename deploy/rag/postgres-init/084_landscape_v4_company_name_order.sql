-- Make company profile snapshot ordering explicit and reproducible.

BEGIN;

ALTER TABLE landscape_v4_company_names
    ADD COLUMN IF NOT EXISTS sort_order INTEGER;

WITH ranked AS (
    SELECT profile_id,profile_version,name_id,
           row_number() OVER (
               PARTITION BY profile_id,profile_version
               ORDER BY created_at,name_id
           ) AS position
    FROM landscape_v4_company_names
)
UPDATE landscape_v4_company_names AS names
SET sort_order=ranked.position
FROM ranked
WHERE names.profile_id=ranked.profile_id
  AND names.profile_version=ranked.profile_version
  AND names.name_id=ranked.name_id
  AND names.sort_order IS NULL;

ALTER TABLE landscape_v4_company_names
    ALTER COLUMN sort_order SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_landscape_v4_company_names_sort_order'
    ) THEN
        ALTER TABLE landscape_v4_company_names
            ADD CONSTRAINT ck_landscape_v4_company_names_sort_order
            CHECK (sort_order > 0);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_landscape_v4_company_names_sort_order'
    ) THEN
        ALTER TABLE landscape_v4_company_names
            ADD CONSTRAINT uq_landscape_v4_company_names_sort_order
            UNIQUE(profile_id,profile_version,sort_order);
    END IF;
END;
$$;

INSERT INTO aifpatent_schema_migrations(version)
VALUES ('084_landscape_v4_company_name_order')
ON CONFLICT (version) DO NOTHING;

COMMIT;
