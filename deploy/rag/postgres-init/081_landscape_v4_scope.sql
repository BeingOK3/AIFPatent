-- Clean-slate Landscape v4 company memory and reviewable scope snapshots.

BEGIN;

CREATE TABLE IF NOT EXISTS landscape_v4_company_profiles (
    profile_id TEXT PRIMARY KEY CHECK (profile_id ~ '^CMP-[0-9a-f]{16}$'),
    anchor_normalized TEXT NOT NULL UNIQUE CHECK (length(btrim(anchor_normalized)) > 0),
    display_name TEXT NOT NULL CHECK (length(btrim(display_name)) > 0),
    current_version INTEGER NOT NULL DEFAULT 0 CHECK (current_version >= 0),
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS landscape_v4_company_profile_versions (
    profile_id TEXT NOT NULL
        REFERENCES landscape_v4_company_profiles(profile_id) ON DELETE RESTRICT,
    version INTEGER NOT NULL CHECK (version >= 1),
    snapshot_hash TEXT NOT NULL CHECK (snapshot_hash ~ '^[0-9a-f]{64}$'),
    confirmed_scope_revision_id TEXT,
    created_at BIGINT NOT NULL,
    PRIMARY KEY(profile_id, version),
    UNIQUE(profile_id, snapshot_hash)
);

CREATE TABLE IF NOT EXISTS landscape_v4_company_names (
    profile_id TEXT NOT NULL,
    profile_version INTEGER NOT NULL,
    name_id TEXT NOT NULL CHECK (name_id ~ '^CNM-[0-9a-f]{16}$'),
    name_text TEXT NOT NULL CHECK (length(btrim(name_text)) > 0),
    normalized_text TEXT NOT NULL CHECK (length(btrim(normalized_text)) > 0),
    language TEXT NOT NULL CHECK (language IN ('ZH','EN','OTHER')),
    relation_type TEXT NOT NULL CHECK (relation_type IN (
        'LEGAL_NAME','TRANSLATION','ALIAS','FORMER_NAME','SUBSIDIARY','GROUP_MEMBER'
    )),
    source TEXT NOT NULL CHECK (source IN (
        'USER_INPUT','USER_ADDED','HISTORY','MODEL_SUGGESTED'
    )),
    status TEXT NOT NULL CHECK (status IN ('ACTIVE','EXCLUDED')),
    memory_action TEXT NOT NULL DEFAULT 'NONE'
        CHECK (memory_action IN ('NONE','REJECT','RETIRE')),
    rationale TEXT,
    sort_order INTEGER NOT NULL CHECK (sort_order > 0),
    created_at BIGINT NOT NULL,
    PRIMARY KEY(profile_id, profile_version, name_id),
    UNIQUE(profile_id, profile_version, normalized_text),
    UNIQUE(profile_id, profile_version, sort_order),
    FOREIGN KEY(profile_id, profile_version)
        REFERENCES landscape_v4_company_profile_versions(profile_id, version)
        ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_landscape_v4_company_names_lookup
    ON landscape_v4_company_names(normalized_text, status, profile_version);

CREATE TABLE IF NOT EXISTS landscape_v4_scope_drafts (
    draft_id TEXT PRIMARY KEY CHECK (draft_id ~ '^SCD-[0-9a-f]{16}$'),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    status TEXT NOT NULL CHECK (status IN (
        'DRAFT','EXPANDING','AWAITING_CONFIRMATION','CONFIRMED'
    )),
    mode TEXT NOT NULL CHECK (mode IN (
        'COMPANY_ONLY','TECHNOLOGY_ONLY','COMPANY_AND_TECHNOLOGY'
    )),
    publication_start DATE NOT NULL,
    publication_end DATE NOT NULL,
    technology_input TEXT,
    content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    confirmed_scope_revision_id TEXT,
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL,
    CONSTRAINT ck_landscape_v4_scope_drafts_date_range
        CHECK (publication_end >= publication_start)
);
CREATE INDEX IF NOT EXISTS idx_landscape_v4_scope_drafts_updated
    ON landscape_v4_scope_drafts(updated_at DESC);

CREATE TABLE IF NOT EXISTS landscape_v4_scope_draft_revisions (
    draft_id TEXT NOT NULL
        REFERENCES landscape_v4_scope_drafts(draft_id) ON DELETE RESTRICT,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    status TEXT NOT NULL CHECK (status IN (
        'DRAFT','EXPANDING','AWAITING_CONFIRMATION','CONFIRMED'
    )),
    mode TEXT NOT NULL CHECK (mode IN (
        'COMPANY_ONLY','TECHNOLOGY_ONLY','COMPANY_AND_TECHNOLOGY'
    )),
    publication_start DATE NOT NULL,
    publication_end DATE NOT NULL,
    technology_input TEXT,
    content_json JSONB NOT NULL,
    content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    created_at BIGINT NOT NULL,
    PRIMARY KEY(draft_id, revision),
    CONSTRAINT ck_landscape_v4_scope_draft_revisions_date_range
        CHECK (publication_end >= publication_start)
);

CREATE TABLE IF NOT EXISTS landscape_v4_scope_draft_companies (
    draft_id TEXT NOT NULL,
    draft_revision INTEGER NOT NULL,
    profile_id TEXT NOT NULL CHECK (profile_id ~ '^CMP-[0-9a-f]{16}$'),
    display_name TEXT NOT NULL,
    input_name TEXT NOT NULL,
    sort_order INTEGER NOT NULL CHECK (sort_order > 0),
    PRIMARY KEY(draft_id, draft_revision, profile_id),
    UNIQUE(draft_id, draft_revision, sort_order),
    FOREIGN KEY(draft_id, draft_revision)
        REFERENCES landscape_v4_scope_draft_revisions(draft_id, revision)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS landscape_v4_scope_draft_names (
    draft_id TEXT NOT NULL,
    draft_revision INTEGER NOT NULL,
    profile_id TEXT NOT NULL,
    name_id TEXT NOT NULL CHECK (name_id ~ '^CNM-[0-9a-f]{16}$'),
    name_text TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    language TEXT NOT NULL CHECK (language IN ('ZH','EN','OTHER')),
    relation_type TEXT NOT NULL CHECK (relation_type IN (
        'LEGAL_NAME','TRANSLATION','ALIAS','FORMER_NAME','SUBSIDIARY','GROUP_MEMBER'
    )),
    source TEXT NOT NULL CHECK (source IN (
        'USER_INPUT','USER_ADDED','HISTORY','MODEL_SUGGESTED'
    )),
    status TEXT NOT NULL CHECK (status IN ('PROPOSED','ACTIVE','EXCLUDED')),
    memory_action TEXT NOT NULL DEFAULT 'NONE'
        CHECK (memory_action IN ('NONE','REJECT','RETIRE')),
    rationale TEXT,
    sort_order INTEGER NOT NULL CHECK (sort_order > 0),
    PRIMARY KEY(draft_id, draft_revision, profile_id, name_id),
    UNIQUE(draft_id, draft_revision, profile_id, normalized_text),
    FOREIGN KEY(draft_id, draft_revision, profile_id)
        REFERENCES landscape_v4_scope_draft_companies(draft_id, draft_revision, profile_id)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS landscape_v4_scope_draft_terms (
    draft_id TEXT NOT NULL,
    draft_revision INTEGER NOT NULL,
    term_id TEXT NOT NULL CHECK (term_id ~ '^TRM-[0-9a-f]{16}$'),
    term_text TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    language TEXT NOT NULL CHECK (language IN ('ZH','EN')),
    relation_to_original TEXT NOT NULL CHECK (relation_to_original IN (
        'ORIGINAL','TRANSLATION','SYNONYM','ABBREVIATION',
        'BROADER','NARROWER','COMPONENT','RELATED'
    )),
    source TEXT NOT NULL CHECK (source IN (
        'USER_INPUT','USER_ADDED','HISTORY','MODEL_SUGGESTED'
    )),
    status TEXT NOT NULL CHECK (status IN ('PROPOSED','ACTIVE','EXCLUDED')),
    rationale TEXT,
    sort_order INTEGER NOT NULL CHECK (sort_order > 0),
    PRIMARY KEY(draft_id, draft_revision, term_id),
    UNIQUE(draft_id, draft_revision, normalized_text),
    FOREIGN KEY(draft_id, draft_revision)
        REFERENCES landscape_v4_scope_draft_revisions(draft_id, revision)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS landscape_v4_scope_draft_limitations (
    draft_id TEXT NOT NULL,
    draft_revision INTEGER NOT NULL,
    code TEXT NOT NULL CHECK (code ~ '^[A-Z][A-Z0-9_]{2,63}$'),
    object_key TEXT NOT NULL CHECK (length(btrim(object_key)) > 0),
    message TEXT NOT NULL CHECK (length(btrim(message)) > 0),
    sort_order INTEGER NOT NULL CHECK (sort_order > 0),
    PRIMARY KEY(draft_id,draft_revision,code,object_key),
    UNIQUE(draft_id,draft_revision,sort_order),
    FOREIGN KEY(draft_id,draft_revision)
        REFERENCES landscape_v4_scope_draft_revisions(draft_id,revision)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS landscape_v4_scope_revisions (
    scope_revision_id TEXT PRIMARY KEY CHECK (scope_revision_id ~ '^SCR-[0-9a-f]{16}$'),
    scope_revision_hash TEXT NOT NULL UNIQUE CHECK (scope_revision_hash ~ '^[0-9a-f]{64}$'),
    source_draft_id TEXT NOT NULL
        REFERENCES landscape_v4_scope_drafts(draft_id) ON DELETE RESTRICT,
    source_draft_revision INTEGER NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN (
        'COMPANY_ONLY','TECHNOLOGY_ONLY','COMPANY_AND_TECHNOLOGY'
    )),
    publication_start DATE NOT NULL,
    publication_end DATE NOT NULL,
    technology_input TEXT,
    snapshot_json JSONB NOT NULL,
    created_at BIGINT NOT NULL,
    FOREIGN KEY(source_draft_id, source_draft_revision)
        REFERENCES landscape_v4_scope_draft_revisions(draft_id, revision)
        ON DELETE RESTRICT,
    CONSTRAINT ck_landscape_v4_scope_revisions_date_range
        CHECK (publication_end >= publication_start)
);

-- The two links below close intentional creation cycles. They are deferred so a
-- confirmation transaction can insert the immutable scope and company versions
-- in either order, while PostgreSQL still rejects an incomplete commit.
DO $$
BEGIN
    -- Converge databases that ran a pre-release draft of this migration.
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'landscape_v4_scope_drafts'
          AND column_name = 'publication_start'
          AND data_type <> 'date'
    ) THEN
        ALTER TABLE landscape_v4_scope_drafts
            ALTER COLUMN publication_start TYPE DATE USING publication_start::date,
            ALTER COLUMN publication_end TYPE DATE USING publication_end::date;
        ALTER TABLE landscape_v4_scope_draft_revisions
            ALTER COLUMN publication_start TYPE DATE USING publication_start::date,
            ALTER COLUMN publication_end TYPE DATE USING publication_end::date;
        ALTER TABLE landscape_v4_scope_revisions
            ALTER COLUMN publication_start TYPE DATE USING publication_start::date,
            ALTER COLUMN publication_end TYPE DATE USING publication_end::date;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_landscape_v4_scope_drafts_date_range'
    ) THEN
        ALTER TABLE landscape_v4_scope_drafts
            ADD CONSTRAINT ck_landscape_v4_scope_drafts_date_range
            CHECK (publication_end >= publication_start);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_landscape_v4_scope_draft_revisions_date_range'
    ) THEN
        ALTER TABLE landscape_v4_scope_draft_revisions
            ADD CONSTRAINT ck_landscape_v4_scope_draft_revisions_date_range
            CHECK (publication_end >= publication_start);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_landscape_v4_scope_revisions_date_range'
    ) THEN
        ALTER TABLE landscape_v4_scope_revisions
            ADD CONSTRAINT ck_landscape_v4_scope_revisions_date_range
            CHECK (publication_end >= publication_start);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_landscape_v4_profile_version_scope_revision'
    ) THEN
        ALTER TABLE landscape_v4_company_profile_versions
            ADD CONSTRAINT fk_landscape_v4_profile_version_scope_revision
            FOREIGN KEY(confirmed_scope_revision_id)
            REFERENCES landscape_v4_scope_revisions(scope_revision_id)
            ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_landscape_v4_scope_draft_confirmed_revision'
    ) THEN
        ALTER TABLE landscape_v4_scope_drafts
            ADD CONSTRAINT fk_landscape_v4_scope_draft_confirmed_revision
            FOREIGN KEY(confirmed_scope_revision_id)
            REFERENCES landscape_v4_scope_revisions(scope_revision_id)
            ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED;
    END IF;
END;
$$;

CREATE TABLE IF NOT EXISTS landscape_v4_scope_companies (
    scope_revision_id TEXT NOT NULL
        REFERENCES landscape_v4_scope_revisions(scope_revision_id) ON DELETE RESTRICT,
    profile_id TEXT NOT NULL CHECK (profile_id ~ '^CMP-[0-9a-f]{16}$'),
    profile_version INTEGER NOT NULL CHECK (profile_version >= 1),
    display_name TEXT NOT NULL,
    sort_order INTEGER NOT NULL CHECK (sort_order > 0),
    PRIMARY KEY(scope_revision_id, profile_id),
    UNIQUE(scope_revision_id, sort_order),
    FOREIGN KEY(profile_id, profile_version)
        REFERENCES landscape_v4_company_profile_versions(profile_id, version)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS landscape_v4_scope_company_names (
    scope_revision_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    name_id TEXT NOT NULL CHECK (name_id ~ '^CNM-[0-9a-f]{16}$'),
    name_text TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    language TEXT NOT NULL CHECK (language IN ('ZH','EN','OTHER')),
    relation_type TEXT NOT NULL CHECK (relation_type IN (
        'LEGAL_NAME','TRANSLATION','ALIAS','FORMER_NAME','SUBSIDIARY','GROUP_MEMBER'
    )),
    sort_order INTEGER NOT NULL CHECK (sort_order > 0),
    PRIMARY KEY(scope_revision_id, profile_id, name_id),
    UNIQUE(scope_revision_id, profile_id, normalized_text),
    FOREIGN KEY(scope_revision_id, profile_id)
        REFERENCES landscape_v4_scope_companies(scope_revision_id, profile_id)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS landscape_v4_scope_terms (
    scope_revision_id TEXT NOT NULL
        REFERENCES landscape_v4_scope_revisions(scope_revision_id) ON DELETE RESTRICT,
    term_id TEXT NOT NULL CHECK (term_id ~ '^TRM-[0-9a-f]{16}$'),
    term_text TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    language TEXT NOT NULL CHECK (language IN ('ZH','EN')),
    relation_to_original TEXT NOT NULL CHECK (relation_to_original IN (
        'ORIGINAL','TRANSLATION','SYNONYM','ABBREVIATION',
        'BROADER','NARROWER','COMPONENT','RELATED'
    )),
    sort_order INTEGER NOT NULL CHECK (sort_order > 0),
    PRIMARY KEY(scope_revision_id, term_id),
    UNIQUE(scope_revision_id, normalized_text),
    UNIQUE(scope_revision_id, sort_order)
);

CREATE OR REPLACE FUNCTION prevent_landscape_v4_snapshot_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'Landscape v4 scope and company snapshots are immutable';
END;
$$;

DO $$
DECLARE
    table_name TEXT;
    trigger_name TEXT;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'landscape_v4_company_profile_versions',
        'landscape_v4_company_names',
        'landscape_v4_scope_draft_revisions',
        'landscape_v4_scope_draft_companies',
        'landscape_v4_scope_draft_names',
        'landscape_v4_scope_draft_terms',
        'landscape_v4_scope_draft_limitations',
        'landscape_v4_scope_revisions',
        'landscape_v4_scope_companies',
        'landscape_v4_scope_company_names',
        'landscape_v4_scope_terms'
    ] LOOP
        trigger_name := 'trg_' || table_name || '_immutable';
        IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = trigger_name) THEN
            EXECUTE format(
                'CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON %I '
                'FOR EACH ROW EXECUTE FUNCTION prevent_landscape_v4_snapshot_mutation()',
                trigger_name,
                table_name
            );
        END IF;
    END LOOP;
END;
$$;

INSERT INTO aifpatent_schema_migrations(version)
VALUES ('081_landscape_v4_scope')
ON CONFLICT (version) DO NOTHING;

COMMIT;
