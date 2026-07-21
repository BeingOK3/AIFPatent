BEGIN;

CREATE TABLE IF NOT EXISTS followup_threads (
    thread_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES idea_runs(run_id) ON DELETE CASCADE,
    title TEXT NOT NULL CHECK (btrim(title) <> ''),
    status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'ARCHIVED')),
    default_mode TEXT NOT NULL CHECK (
        default_mode IN ('EVIDENCE_QA', 'DESIGN_AROUND', 'NEW_RESEARCH')
    ),
    scope_json JSONB NOT NULL,
    corpus_snapshot_hash TEXT NOT NULL CHECK (
        corpus_snapshot_hash ~ '^[0-9a-f]{64}$'
    ),
    created_at BIGINT NOT NULL,
    updated_at BIGINT NOT NULL CHECK (updated_at >= created_at)
);
CREATE INDEX IF NOT EXISTS idx_followup_threads_run_created
ON followup_threads(run_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_followup_threads_status_updated
ON followup_threads(status, updated_at DESC);

CREATE TABLE IF NOT EXISTS followup_turns (
    turn_id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES followup_threads(thread_id) ON DELETE CASCADE,
    parent_turn_id TEXT,
    status TEXT NOT NULL CHECK (
        status IN (
            'QUEUED', 'RUNNING', 'COMPLETED',
            'COMPLETED_WITH_LIMITATIONS', 'FAILED', 'CANCELLED'
        )
    ),
    mode TEXT NOT NULL CHECK (
        mode IN ('EVIDENCE_QA', 'DESIGN_AROUND', 'NEW_RESEARCH')
    ),
    question_text TEXT NOT NULL CHECK (btrim(question_text) <> ''),
    question_hash TEXT NOT NULL CHECK (question_hash ~ '^[0-9a-f]{64}$'),
    scope_json JSONB NOT NULL,
    plan_json JSONB,
    answer_json JSONB,
    model TEXT NOT NULL CHECK (btrim(model) <> ''),
    prompt_version TEXT NOT NULL CHECK (btrim(prompt_version) <> ''),
    retriever_version TEXT NOT NULL CHECK (btrim(retriever_version) <> ''),
    corpus_snapshot_hash TEXT NOT NULL CHECK (
        corpus_snapshot_hash ~ '^[0-9a-f]{64}$'
    ),
    limitations_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    error_code TEXT,
    error_message TEXT,
    created_at BIGINT NOT NULL,
    started_at BIGINT,
    completed_at BIGINT,
    UNIQUE(thread_id, turn_id),
    FOREIGN KEY(thread_id, parent_turn_id)
        REFERENCES followup_turns(thread_id, turn_id) ON DELETE RESTRICT,
    CHECK (started_at IS NULL OR started_at >= created_at),
    CHECK (completed_at IS NULL OR completed_at >= created_at),
    CHECK (
        status NOT IN ('COMPLETED', 'COMPLETED_WITH_LIMITATIONS')
        OR (answer_json IS NOT NULL AND completed_at IS NOT NULL)
    ),
    CHECK (
        status <> 'FAILED'
        OR (error_code IS NOT NULL AND completed_at IS NOT NULL)
    )
);
CREATE INDEX IF NOT EXISTS idx_followup_turns_thread_created
ON followup_turns(thread_id, created_at, turn_id);
CREATE INDEX IF NOT EXISTS idx_followup_turns_status_created
ON followup_turns(status, created_at);

CREATE TABLE IF NOT EXISTS followup_retrieval_hits (
    turn_id TEXT NOT NULL REFERENCES followup_turns(turn_id) ON DELETE CASCADE,
    chunk_id TEXT NOT NULL REFERENCES patent_chunks(chunk_id) ON DELETE RESTRICT,
    lexical_rank INTEGER CHECK (lexical_rank IS NULL OR lexical_rank > 0),
    vector_rank INTEGER CHECK (vector_rank IS NULL OR vector_rank > 0),
    rrf_score DOUBLE PRECISION CHECK (rrf_score IS NULL OR rrf_score = rrf_score),
    rerank_score DOUBLE PRECISION CHECK (
        rerank_score IS NULL OR rerank_score = rerank_score
    ),
    final_rank INTEGER NOT NULL CHECK (final_rank > 0),
    query_sources_json JSONB NOT NULL,
    selected_for_context BOOLEAN NOT NULL,
    PRIMARY KEY(turn_id, chunk_id),
    UNIQUE(turn_id, final_rank),
    CHECK (lexical_rank IS NOT NULL OR vector_rank IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_followup_hits_turn_selected
ON followup_retrieval_hits(turn_id, selected_for_context, final_rank);

CREATE TABLE IF NOT EXISTS followup_citations (
    citation_id TEXT PRIMARY KEY,
    turn_id TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    publication_number TEXT NOT NULL,
    section_type TEXT NOT NULL,
    section_label TEXT NOT NULL,
    quote_text TEXT NOT NULL CHECK (btrim(quote_text) <> ''),
    quote_hash TEXT NOT NULL CHECK (quote_hash ~ '^[0-9a-f]{64}$'),
    start_offset INTEGER,
    end_offset INTEGER,
    answer_path TEXT NOT NULL CHECK (btrim(answer_path) <> ''),
    created_at BIGINT NOT NULL,
    FOREIGN KEY(turn_id, chunk_id)
        REFERENCES followup_retrieval_hits(turn_id, chunk_id) ON DELETE CASCADE,
    CHECK (start_offset IS NULL OR start_offset >= 0),
    CHECK (end_offset IS NULL OR end_offset >= 0),
    CHECK (start_offset IS NULL OR end_offset IS NULL OR end_offset >= start_offset),
    UNIQUE(turn_id, answer_path, chunk_id, quote_hash)
);
CREATE INDEX IF NOT EXISTS idx_followup_citations_turn
ON followup_citations(turn_id, citation_id);

CREATE OR REPLACE FUNCTION prevent_followup_thread_scope_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.run_id IS DISTINCT FROM OLD.run_id
       OR NEW.scope_json IS DISTINCT FROM OLD.scope_json
       OR NEW.corpus_snapshot_hash IS DISTINCT FROM OLD.corpus_snapshot_hash
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'follow-up thread frozen scope is immutable';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_followup_thread_scope_immutable ON followup_threads;
CREATE TRIGGER trg_followup_thread_scope_immutable
BEFORE UPDATE ON followup_threads
FOR EACH ROW EXECUTE FUNCTION prevent_followup_thread_scope_update();

CREATE OR REPLACE FUNCTION enforce_followup_turn_transition()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status IN ('COMPLETED', 'COMPLETED_WITH_LIMITATIONS', 'FAILED', 'CANCELLED') THEN
        IF NEW IS DISTINCT FROM OLD THEN
            RAISE EXCEPTION 'terminal follow-up turn is immutable';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.thread_id IS DISTINCT FROM OLD.thread_id
       OR NEW.parent_turn_id IS DISTINCT FROM OLD.parent_turn_id
       OR NEW.mode IS DISTINCT FROM OLD.mode
       OR NEW.question_text IS DISTINCT FROM OLD.question_text
       OR NEW.question_hash IS DISTINCT FROM OLD.question_hash
       OR NEW.scope_json IS DISTINCT FROM OLD.scope_json
       OR NEW.model IS DISTINCT FROM OLD.model
       OR NEW.prompt_version IS DISTINCT FROM OLD.prompt_version
       OR NEW.retriever_version IS DISTINCT FROM OLD.retriever_version
       OR NEW.corpus_snapshot_hash IS DISTINCT FROM OLD.corpus_snapshot_hash
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'follow-up turn input and frozen scope are immutable';
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
        (OLD.status = 'QUEUED' AND NEW.status IN ('RUNNING', 'FAILED', 'CANCELLED'))
        OR
        (OLD.status = 'RUNNING' AND NEW.status IN (
            'COMPLETED', 'COMPLETED_WITH_LIMITATIONS', 'FAILED', 'CANCELLED'
        ))
    ) THEN
        RAISE EXCEPTION 'invalid follow-up turn status transition: % -> %', OLD.status, NEW.status;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_followup_turn_transition ON followup_turns;
CREATE TRIGGER trg_followup_turn_transition
BEFORE UPDATE ON followup_turns
FOR EACH ROW EXECUTE FUNCTION enforce_followup_turn_transition();

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_model_context_followup_turn'
    ) THEN
        ALTER TABLE model_context_manifests
        ADD CONSTRAINT fk_model_context_followup_turn
        FOREIGN KEY(turn_id) REFERENCES followup_turns(turn_id) ON DELETE CASCADE;
    END IF;
END;
$$;

INSERT INTO aifpatent_schema_migrations(version)
VALUES ('050_followup_schema')
ON CONFLICT (version) DO NOTHING;

COMMIT;
