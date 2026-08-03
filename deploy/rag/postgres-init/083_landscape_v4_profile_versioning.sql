-- Every distinct confirmation creates a new immutable company profile version,
-- even when its content intentionally returns to an older snapshot.

BEGIN;

ALTER TABLE landscape_v4_company_profile_versions
    DROP CONSTRAINT IF EXISTS landscape_v4_company_profile_versi_profile_id_snapshot_hash_key;

INSERT INTO aifpatent_schema_migrations(version)
VALUES ('083_landscape_v4_profile_versioning')
ON CONFLICT (version) DO NOTHING;

COMMIT;
