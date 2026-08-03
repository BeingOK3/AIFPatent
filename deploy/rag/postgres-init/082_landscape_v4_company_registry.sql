-- Atomic ownership registry for normalized Landscape v4 company names.

BEGIN;

CREATE TABLE IF NOT EXISTS landscape_v4_company_name_registry (
    normalized_text TEXT PRIMARY KEY CHECK (length(btrim(normalized_text)) > 0),
    profile_id TEXT NOT NULL,
    profile_version INTEGER NOT NULL CHECK (profile_version >= 1),
    status TEXT NOT NULL CHECK (status IN ('ACTIVE','REJECTED','RETIRED')),
    updated_at BIGINT NOT NULL,
    FOREIGN KEY(profile_id, profile_version)
        REFERENCES landscape_v4_company_profile_versions(profile_id, version)
        ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_landscape_v4_company_registry_profile
    ON landscape_v4_company_name_registry(profile_id, status, normalized_text);

INSERT INTO aifpatent_schema_migrations(version)
VALUES ('082_landscape_v4_company_registry')
ON CONFLICT (version) DO NOTHING;

COMMIT;
