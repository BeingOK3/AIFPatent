-- Persist user-visible Scope expansion limitations per immutable draft revision.

BEGIN;

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

DROP TRIGGER IF EXISTS trg_landscape_v4_scope_draft_limitations_immutable
    ON landscape_v4_scope_draft_limitations;
CREATE TRIGGER trg_landscape_v4_scope_draft_limitations_immutable
BEFORE UPDATE OR DELETE ON landscape_v4_scope_draft_limitations
FOR EACH ROW EXECUTE FUNCTION prevent_landscape_v4_snapshot_mutation();

INSERT INTO aifpatent_schema_migrations(version)
VALUES ('085_landscape_v4_scope_limitations')
ON CONFLICT (version) DO NOTHING;

COMMIT;
