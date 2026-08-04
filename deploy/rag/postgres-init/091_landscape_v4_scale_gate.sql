BEGIN;

CREATE TABLE IF NOT EXISTS landscape_v4_scale_gates (
    run_id TEXT PRIMARY KEY REFERENCES landscape_v4_runs(run_id) ON DELETE RESTRICT,
    scope_revision_id TEXT NOT NULL,
    scope_revision_hash TEXT NOT NULL CHECK (scope_revision_hash ~ '^[0-9a-f]{64}$'),
    publication_start DATE NOT NULL,
    publication_end DATE NOT NULL,
    query_count INTEGER NOT NULL CHECK (query_count > 0),
    estimated_total_results INTEGER NOT NULL CHECK (estimated_total_results >= 0),
    estimated_total_pages INTEGER NOT NULL CHECK (estimated_total_pages >= 0),
    estimated_shards INTEGER NOT NULL CHECK (estimated_shards >= 0),
    tier TEXT NOT NULL CHECK (tier IN (
        'WITHIN_DEFAULT','CONFIRM_MEDIUM','CONFIRM_LARGE'
    )),
    decision TEXT CHECK (decision IN ('APPROVED','REJECTED')),
    created_at BIGINT NOT NULL CHECK (created_at >= 0),
    decided_at BIGINT CHECK (decided_at >= created_at),
    FOREIGN KEY(run_id,scope_revision_id)
        REFERENCES landscape_v4_runs(run_id,scope_revision_id) ON DELETE RESTRICT,
    CHECK ((decision IS NULL) = (decided_at IS NULL))
    ,CHECK (publication_end >= publication_start)
);

CREATE OR REPLACE FUNCTION protect_landscape_v4_scale_gate()
RETURNS trigger AS $$
BEGIN
    IF NEW.run_id IS DISTINCT FROM OLD.run_id
       OR NEW.scope_revision_id IS DISTINCT FROM OLD.scope_revision_id
       OR NEW.scope_revision_hash IS DISTINCT FROM OLD.scope_revision_hash
       OR NEW.publication_start IS DISTINCT FROM OLD.publication_start
       OR NEW.publication_end IS DISTINCT FROM OLD.publication_end
       OR NEW.query_count IS DISTINCT FROM OLD.query_count
       OR NEW.estimated_total_results IS DISTINCT FROM OLD.estimated_total_results
       OR NEW.estimated_total_pages IS DISTINCT FROM OLD.estimated_total_pages
       OR NEW.estimated_shards IS DISTINCT FROM OLD.estimated_shards
       OR NEW.tier IS DISTINCT FROM OLD.tier
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'landscape v4 scale estimate is immutable';
    END IF;
    IF OLD.decision IS NOT NULL THEN
        RAISE EXCEPTION 'landscape v4 scale decision is immutable';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_landscape_v4_scale_gate_protect
    ON landscape_v4_scale_gates;
CREATE TRIGGER trg_landscape_v4_scale_gate_protect
BEFORE UPDATE ON landscape_v4_scale_gates
FOR EACH ROW EXECUTE FUNCTION protect_landscape_v4_scale_gate();

DROP TRIGGER IF EXISTS trg_landscape_v4_scale_gate_no_delete
    ON landscape_v4_scale_gates;
CREATE TRIGGER trg_landscape_v4_scale_gate_no_delete
BEFORE DELETE ON landscape_v4_scale_gates
FOR EACH ROW EXECUTE FUNCTION prevent_landscape_v4_query_plan_mutation();

INSERT INTO aifpatent_schema_migrations(version)
VALUES ('091_landscape_v4_scale_gate')
ON CONFLICT (version) DO NOTHING;

COMMIT;
