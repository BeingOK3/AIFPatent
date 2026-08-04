BEGIN;

CREATE TABLE IF NOT EXISTS landscape_v4_reports (
    run_id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    report_hash TEXT NOT NULL CHECK (report_hash ~ '^[0-9a-f]{64}$'),
    report_json JSONB NOT NULL,
    report_markdown TEXT NOT NULL,
    created_at BIGINT NOT NULL CHECK (created_at >= 0),
    FOREIGN KEY(run_id) REFERENCES landscape_v4_runs(run_id) ON DELETE RESTRICT
);

CREATE OR REPLACE FUNCTION prevent_landscape_v4_report_mutation()
RETURNS trigger AS $$ BEGIN RAISE EXCEPTION 'landscape v4 reports are immutable'; END; $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_landscape_v4_reports_immutable ON landscape_v4_reports;
CREATE TRIGGER trg_landscape_v4_reports_immutable
BEFORE UPDATE OR DELETE ON landscape_v4_reports
FOR EACH ROW EXECUTE FUNCTION prevent_landscape_v4_report_mutation();

INSERT INTO aifpatent_schema_migrations(version)
VALUES ('101_landscape_v4_reports')
ON CONFLICT (version) DO NOTHING;

COMMIT;
