from __future__ import annotations

import json
import hashlib
import os
import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from .database import now_ms
from .followup import (
    FollowupCitation,
    FollowupCitationDraft,
    FollowupError,
    FollowupMode,
    FollowupScope,
    FollowupScopeDocument,
    FollowupThread,
    FollowupTurn,
    ThreadStatus,
    TurnStatus,
    question_hash,
)
from .hybrid import HybridSearchResult
from .merge import normalize_publication_number


Connect = Callable[[str], Awaitable[Any]]


def _encoded(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decoded(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


class PostgreSQLFollowupRepository:
    """Append-oriented Thread/Turn storage with frozen source-Run evidence scope."""

    def __init__(self, dsn: str | None = None, *, connect: Connect | None = None) -> None:
        self.dsn = (dsn or os.environ.get("AIFPATENT_POSTGRES_DSN") or "").strip()
        if not self.dsn:
            raise ValueError("PostgreSQL follow-up repository requires a DSN")
        self._connect = connect

    async def _connection(self) -> Any:
        if self._connect is not None:
            return await self._connect(self.dsn)
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover
            raise FollowupError("psycopg is required for follow-up persistence") from exc
        return await psycopg.AsyncConnection.connect(self.dsn, row_factory=dict_row)

    async def create_thread(
        self,
        *,
        run_id: str,
        title: str,
        default_mode: FollowupMode = FollowupMode.EVIDENCE_QA,
        publication_numbers: Sequence[str] = (),
    ) -> FollowupThread:
        if not run_id.strip() or not title.strip():
            raise FollowupError("source Run ID and Thread title are required")
        requested = self._normalize_publications(publication_numbers)
        connection = await self._connection()
        try:
            async with connection.cursor() as cursor:
                scope = await self._load_source_scope(cursor, run_id)
                scope = scope.select_publications(requested)
                timestamp = now_ms()
                thread_id = "FT-" + uuid.uuid4().hex
                await cursor.execute(
                    """
                    INSERT INTO followup_threads(
                        thread_id, run_id, title, status, default_mode, scope_json,
                        corpus_snapshot_hash, created_at, updated_at
                    ) VALUES (%s, %s, %s, 'ACTIVE', %s, %s::jsonb, %s, %s, %s)
                    RETURNING thread_id, run_id, title, status, default_mode,
                              scope_json, corpus_snapshot_hash, created_at, updated_at
                    """,
                    (
                        thread_id,
                        run_id,
                        title.strip(),
                        default_mode.value,
                        _encoded(scope.as_json()),
                        scope.corpus_snapshot_hash,
                        timestamp,
                        timestamp,
                    ),
                )
                row = await cursor.fetchone()
            await connection.commit()
            return self._thread(row)
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def get_thread(self, thread_id: str) -> FollowupThread | None:
        connection = await self._connection()
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT thread_id, run_id, title, status, default_mode,
                           scope_json, corpus_snapshot_hash, created_at, updated_at
                    FROM followup_threads WHERE thread_id = %s
                    """,
                    (thread_id,),
                )
                row = await cursor.fetchone()
            return None if row is None else self._thread(row)
        finally:
            await connection.close()

    async def list_threads(self, run_id: str | None = None) -> tuple[FollowupThread, ...]:
        connection = await self._connection()
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT thread_id, run_id, title, status, default_mode,
                           scope_json, corpus_snapshot_hash, created_at, updated_at
                    FROM followup_threads
                    WHERE (%s::text IS NULL OR run_id = %s)
                    ORDER BY updated_at DESC, thread_id
                    """,
                    (run_id, run_id),
                )
                rows = await cursor.fetchall()
            return tuple(self._thread(row) for row in rows)
        finally:
            await connection.close()

    async def archive_thread(self, thread_id: str) -> FollowupThread:
        return await self._update_thread_status(thread_id, ThreadStatus.ARCHIVED)

    async def create_turn(
        self,
        *,
        thread_id: str,
        question: str,
        model: str,
        prompt_version: str,
        retriever_version: str,
        mode: FollowupMode | None = None,
        publication_numbers: Sequence[str] = (),
        parent_turn_id: str | None = None,
    ) -> FollowupTurn:
        if not question.strip() or not model.strip():
            raise FollowupError("follow-up question and model are required")
        if not prompt_version.strip() or not retriever_version.strip():
            raise FollowupError("follow-up Prompt and Retriever versions are required")
        requested = self._normalize_publications(publication_numbers)
        connection = await self._connection()
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT thread_id, run_id, title, status, default_mode,
                           scope_json, corpus_snapshot_hash, created_at, updated_at
                    FROM followup_threads WHERE thread_id = %s FOR UPDATE
                    """,
                    (thread_id,),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise FollowupError("follow-up Thread does not exist")
                thread = self._thread(row)
                if thread.status != ThreadStatus.ACTIVE:
                    raise FollowupError("cannot add a Turn to an archived Thread")
                scope = thread.scope.select_publications(requested)
                if parent_turn_id is not None:
                    await cursor.execute(
                        """
                        SELECT status FROM followup_turns
                        WHERE turn_id = %s AND thread_id = %s
                        """,
                        (parent_turn_id, thread_id),
                    )
                    parent = await cursor.fetchone()
                    parent_status = None if parent is None else str(
                        parent["status"] if isinstance(parent, dict) else parent[0]
                    )
                    if parent_status not in {
                        TurnStatus.COMPLETED.value,
                        TurnStatus.COMPLETED_WITH_LIMITATIONS.value,
                    }:
                        raise FollowupError(
                            "parent Turn must be a completed Turn in the same Thread"
                        )
                timestamp = now_ms()
                turn_id = "FU-" + uuid.uuid4().hex
                await cursor.execute(
                    """
                    INSERT INTO followup_turns(
                        turn_id, thread_id, parent_turn_id, status, mode,
                        question_text, question_hash, scope_json, plan_json,
                        answer_json, model, prompt_version, retriever_version,
                        corpus_snapshot_hash, limitations_json, created_at
                    ) VALUES (
                        %s, %s, %s, 'QUEUED', %s, %s, %s, %s::jsonb,
                        NULL, NULL, %s, %s, %s, %s, '[]'::jsonb, %s
                    )
                    RETURNING *
                    """,
                    (
                        turn_id,
                        thread_id,
                        parent_turn_id,
                        (mode or thread.default_mode).value,
                        question.strip(),
                        question_hash(question),
                        _encoded(scope.as_json()),
                        model.strip(),
                        prompt_version.strip(),
                        retriever_version.strip(),
                        scope.corpus_snapshot_hash,
                        timestamp,
                    ),
                )
                turn_row = await cursor.fetchone()
                await cursor.execute(
                    "UPDATE followup_threads SET updated_at = %s WHERE thread_id = %s",
                    (timestamp, thread_id),
                )
            await connection.commit()
            return self._turn(turn_row)
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def get_turn(self, turn_id: str) -> FollowupTurn | None:
        connection = await self._connection()
        try:
            async with connection.cursor() as cursor:
                await cursor.execute("SELECT * FROM followup_turns WHERE turn_id = %s", (turn_id,))
                row = await cursor.fetchone()
            return None if row is None else self._turn(row)
        finally:
            await connection.close()

    async def record_retrieval(
        self, turn_id: str, result: HybridSearchResult
    ) -> int:
        if len({hit.chunk.chunk_id for hit in result.hits}) != len(result.hits):
            raise FollowupError("follow-up retrieval contains duplicate Chunk IDs")
        connection = await self._connection()
        try:
            async with connection.cursor() as cursor:
                turn = await self._locked_running_turn(cursor, turn_id)
                if result.retriever_version != turn.retriever_version:
                    raise FollowupError("retrieval result version does not match the Turn")
                chunk_ids = [hit.chunk.chunk_id for hit in result.hits]
                if chunk_ids:
                    await cursor.execute(
                        "SELECT chunk_id, version_id FROM patent_chunks WHERE chunk_id = ANY(%s)",
                        (chunk_ids,),
                    )
                    rows = await cursor.fetchall()
                    actual = {
                        str(row["chunk_id"]): str(row["version_id"])
                        for row in rows
                    }
                    if set(actual) != set(chunk_ids):
                        raise FollowupError("retrieval result references a missing Chunk")
                    if any(
                        version_id not in turn.scope.version_ids
                        for version_id in actual.values()
                    ):
                        raise FollowupError("retrieval result escaped the Turn Version scope")
                await cursor.execute(
                    "SELECT COUNT(*) AS count FROM followup_citations WHERE turn_id = %s",
                    (turn_id,),
                )
                citation_count = int((await cursor.fetchone())["count"])
                if citation_count:
                    raise FollowupError("cannot replace retrieval after Citations exist")
                await cursor.execute(
                    "DELETE FROM followup_retrieval_hits WHERE turn_id = %s",
                    (turn_id,),
                )
                for final_rank, hit in enumerate(result.hits, start=1):
                    await cursor.execute(
                        """
                        INSERT INTO followup_retrieval_hits(
                            turn_id, chunk_id, lexical_rank, vector_rank,
                            rrf_score, rerank_score, final_rank,
                            query_sources_json, selected_for_context
                        ) VALUES (%s, %s, %s, %s, %s, NULL, %s, %s::jsonb, TRUE)
                        """,
                        (
                            turn_id,
                            hit.chunk.chunk_id,
                            hit.lexical_rank,
                            hit.vector_rank,
                            hit.rrf_score,
                            final_rank,
                            _encoded(
                                {
                                    "query_id": result.query_id,
                                    "sources": list(hit.sources),
                                    "mode": result.mode.value,
                                    "retriever_version": result.retriever_version,
                                }
                            ),
                        ),
                    )
            await connection.commit()
            return len(result.hits)
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def record_citations(
        self, turn_id: str, drafts: Sequence[FollowupCitationDraft]
    ) -> tuple[FollowupCitation, ...]:
        if not drafts:
            return ()
        identities = [(item.answer_path, item.chunk_id) for item in drafts]
        if len(set(identities)) != len(identities):
            raise FollowupError("follow-up Citation answer paths and Chunks must be unique")
        connection = await self._connection()
        try:
            async with connection.cursor() as cursor:
                turn = await self._locked_running_turn(cursor, turn_id)
                chunk_ids = list(dict.fromkeys(item.chunk_id for item in drafts))
                await cursor.execute(
                    """
                    SELECT c.chunk_id, c.version_id, c.publication_number,
                           c.section_type, c.section_label, c.start_offset,
                           c.end_offset, c.text
                    FROM followup_retrieval_hits h
                    JOIN patent_chunks c ON c.chunk_id = h.chunk_id
                    WHERE h.turn_id = %s AND h.selected_for_context = TRUE
                      AND c.chunk_id = ANY(%s)
                    """,
                    (turn_id, chunk_ids),
                )
                chunks = {str(row["chunk_id"]): row for row in await cursor.fetchall()}
                if set(chunks) != set(chunk_ids):
                    raise FollowupError("Citation references an unselected retrieval Chunk")
                citations: list[FollowupCitation] = []
                for draft in drafts:
                    row = chunks[draft.chunk_id]
                    if str(row["version_id"]) not in turn.scope.version_ids:
                        raise FollowupError("Citation escaped the Turn Version scope")
                    self._verify_quote(draft, row)
                    quote_hash = hashlib.sha256(draft.quote_text.encode("utf-8")).hexdigest()
                    identity = f"{turn_id}|{draft.chunk_id}|{draft.answer_path}|{quote_hash}"
                    citation = FollowupCitation(
                        citation_id="FC-" + uuid.uuid5(uuid.NAMESPACE_URL, identity).hex,
                        turn_id=turn_id,
                        chunk_id=draft.chunk_id,
                        publication_number=str(row["publication_number"]),
                        section_type=str(row["section_type"]),
                        section_label=str(row["section_label"]),
                        quote_text=draft.quote_text,
                        quote_hash=quote_hash,
                        start_offset=draft.start_offset,
                        end_offset=draft.end_offset,
                        answer_path=draft.answer_path,
                    )
                    await cursor.execute(
                        """
                        INSERT INTO followup_citations(
                            citation_id, turn_id, chunk_id, publication_number,
                            section_type, section_label, quote_text, quote_hash,
                            start_offset, end_offset, answer_path, created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (citation_id) DO UPDATE
                        SET citation_id = EXCLUDED.citation_id
                        """,
                        (
                            citation.citation_id, citation.turn_id, citation.chunk_id,
                            citation.publication_number, citation.section_type,
                            citation.section_label, citation.quote_text,
                            citation.quote_hash, citation.start_offset,
                            citation.end_offset, citation.answer_path, now_ms(),
                        ),
                    )
                    citations.append(citation)
            await connection.commit()
            return tuple(citations)
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def start_turn(self, turn_id: str) -> FollowupTurn:
        return await self._transition(
            turn_id,
            """
            UPDATE followup_turns SET status = 'RUNNING', started_at = %s
            WHERE turn_id = %s AND status = 'QUEUED'
            RETURNING *
            """,
            (now_ms(), turn_id),
        )

    async def save_plan(self, turn_id: str, plan: dict[str, Any]) -> FollowupTurn:
        if not isinstance(plan, dict) or not plan:
            raise FollowupError("follow-up retrieval plan must be a non-empty object")
        return await self._transition(
            turn_id,
            """
            UPDATE followup_turns SET plan_json = %s::jsonb
            WHERE turn_id = %s AND status = 'RUNNING'
            RETURNING *
            """,
            (_encoded(plan), turn_id),
        )

    async def complete_turn(
        self,
        turn_id: str,
        *,
        answer: dict[str, Any],
        limitations: Sequence[str] = (),
    ) -> FollowupTurn:
        if not isinstance(answer, dict) or not answer:
            raise FollowupError("follow-up answer must be a non-empty object")
        normalized_limitations = tuple(
            dict.fromkeys(value.strip() for value in limitations if value.strip())
        )
        status = (
            TurnStatus.COMPLETED_WITH_LIMITATIONS
            if normalized_limitations
            else TurnStatus.COMPLETED
        )
        return await self._transition(
            turn_id,
            """
            UPDATE followup_turns
            SET status = %s, answer_json = %s::jsonb,
                limitations_json = %s::jsonb, completed_at = %s
            WHERE turn_id = %s AND status = 'RUNNING'
            RETURNING *
            """,
            (
                status.value,
                _encoded(answer),
                _encoded(list(normalized_limitations)),
                now_ms(),
                turn_id,
            ),
        )

    async def fail_turn(
        self, turn_id: str, *, error_code: str, error_message: str
    ) -> FollowupTurn:
        code = error_code.strip()
        message = " ".join(error_message.split())[:1000]
        if not code or not message:
            raise FollowupError("follow-up failure requires a safe code and message")
        return await self._transition(
            turn_id,
            """
            UPDATE followup_turns
            SET status = 'FAILED', error_code = %s, error_message = %s, completed_at = %s
            WHERE turn_id = %s AND status IN ('QUEUED', 'RUNNING')
            RETURNING *
            """,
            (code, message, now_ms(), turn_id),
        )

    async def cancel_turn(self, turn_id: str) -> FollowupTurn:
        return await self._transition(
            turn_id,
            """
            UPDATE followup_turns SET status = 'CANCELLED', completed_at = %s
            WHERE turn_id = %s AND status IN ('QUEUED', 'RUNNING')
            RETURNING *
            """,
            (now_ms(), turn_id),
        )

    async def _transition(
        self, turn_id: str, sql: str, parameters: tuple[Any, ...]
    ) -> FollowupTurn:
        connection = await self._connection()
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(sql, parameters)
                row = await cursor.fetchone()
                if row is None:
                    raise FollowupError("follow-up Turn transition is invalid or already terminal")
            await connection.commit()
            return self._turn(row)
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def _locked_running_turn(self, cursor: Any, turn_id: str) -> FollowupTurn:
        await cursor.execute(
            "SELECT * FROM followup_turns WHERE turn_id = %s FOR UPDATE",
            (turn_id,),
        )
        row = await cursor.fetchone()
        if row is None or str(row["status"]) != TurnStatus.RUNNING.value:
            raise FollowupError("follow-up evidence can only change while the Turn is RUNNING")
        return self._turn(row)

    @staticmethod
    def _verify_quote(draft: FollowupCitationDraft, row: dict[str, Any]) -> None:
        text = str(row["text"])
        chunk_start = row.get("start_offset")
        chunk_end = row.get("end_offset")
        if chunk_start is None or chunk_end is None:
            if draft.start_offset is not None or draft.quote_text != text:
                raise FollowupError(
                    "Citation for an unpositioned Chunk must quote the complete Chunk"
                )
            return
        if draft.start_offset is None or draft.end_offset is None:
            raise FollowupError("Citation for a positioned Chunk requires exact offsets")
        relative_start = draft.start_offset - int(chunk_start)
        relative_end = draft.end_offset - int(chunk_start)
        if relative_start < 0 or relative_end > len(text):
            raise FollowupError("Citation offsets escape the selected Chunk")
        if text[relative_start:relative_end] != draft.quote_text:
            raise FollowupError("Citation quote does not match the selected Chunk text")

    async def _update_thread_status(
        self, thread_id: str, status: ThreadStatus
    ) -> FollowupThread:
        connection = await self._connection()
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    UPDATE followup_threads SET status = %s, updated_at = %s
                    WHERE thread_id = %s
                    RETURNING thread_id, run_id, title, status, default_mode,
                              scope_json, corpus_snapshot_hash, created_at, updated_at
                    """,
                    (status.value, now_ms(), thread_id),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise FollowupError("follow-up Thread does not exist")
            await connection.commit()
            return self._thread(row)
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    @staticmethod
    async def _load_source_scope(cursor: Any, run_id: str) -> FollowupScope:
        await cursor.execute("SELECT status FROM idea_runs WHERE run_id = %s", (run_id,))
        row = await cursor.fetchone()
        if row is None:
            raise FollowupError("source Run does not exist")
        status = str(row["status"] if isinstance(row, dict) else row[0])
        if status not in {
            TurnStatus.COMPLETED.value,
            TurnStatus.COMPLETED_WITH_LIMITATIONS.value,
        }:
            raise FollowupError("source Run is not in a follow-up eligible terminal state")
        await cursor.execute(
            """
            SELECT rdv.document_id, rdv.version_id, pd.publication_number,
                   pv.normalized_content_hash
            FROM run_document_versions rdv
            JOIN patent_document_versions pv ON pv.version_id = rdv.version_id
            JOIN patent_documents pd ON pd.document_id = rdv.document_id
            WHERE rdv.run_id = %s
              AND rdv.deep_reviewed = TRUE
              AND rdv.corpus_availability = 'READY'
              AND pv.state = 'READY'
            ORDER BY pd.publication_number, rdv.version_id
            """,
            (run_id,),
        )
        rows = await cursor.fetchall()
        documents = []
        for row in rows:
            if isinstance(row, dict):
                documents.append(
                    FollowupScopeDocument(
                        document_id=str(row["document_id"]),
                        version_id=str(row["version_id"]),
                        publication_number=str(row["publication_number"]),
                        content_sha256=str(row["normalized_content_hash"]),
                    )
                )
            else:
                documents.append(
                    FollowupScopeDocument(
                        document_id=str(row[0]),
                        version_id=str(row[1]),
                        publication_number=str(row[2]),
                        content_sha256=str(row[3]),
                    )
                )
        return FollowupScope.freeze(documents)

    @staticmethod
    def _normalize_publications(values: Sequence[str]) -> tuple[str, ...]:
        normalized = []
        for value in values:
            publication = normalize_publication_number(value)
            if publication is None:
                raise FollowupError("selected publication number is invalid")
            normalized.append(publication)
        if len(set(normalized)) != len(normalized):
            raise FollowupError("selected publication numbers must be unique")
        return tuple(normalized)

    @staticmethod
    def _thread(row: Any) -> FollowupThread:
        fields = (
            "thread_id", "run_id", "title", "status", "default_mode",
            "scope_json", "corpus_snapshot_hash", "created_at", "updated_at",
        )
        values = row if isinstance(row, dict) else dict(zip(fields, row, strict=True))
        scope_json = _decoded(values["scope_json"])
        return FollowupThread(
            thread_id=str(values["thread_id"]),
            run_id=str(values["run_id"]),
            title=str(values["title"]),
            status=ThreadStatus(str(values["status"])),
            default_mode=FollowupMode(str(values["default_mode"])),
            scope=FollowupScope.from_json(
                scope_json, str(values["corpus_snapshot_hash"])
            ),
            created_at=int(values["created_at"]),
            updated_at=int(values["updated_at"]),
        )

    @staticmethod
    def _turn(row: Any) -> FollowupTurn:
        if not isinstance(row, dict):
            raise FollowupError("follow-up Turn adapter requires named PostgreSQL rows")
        scope = FollowupScope.from_json(
            _decoded(row["scope_json"]), str(row["corpus_snapshot_hash"])
        )
        return FollowupTurn(
            turn_id=str(row["turn_id"]),
            thread_id=str(row["thread_id"]),
            parent_turn_id=(
                str(row["parent_turn_id"]) if row.get("parent_turn_id") is not None else None
            ),
            status=TurnStatus(str(row["status"])),
            mode=FollowupMode(str(row["mode"])),
            question_text=str(row["question_text"]),
            question_hash=str(row["question_hash"]),
            scope=scope,
            plan=_decoded(row.get("plan_json")),
            answer=_decoded(row.get("answer_json")),
            model=str(row["model"]),
            prompt_version=str(row["prompt_version"]),
            retriever_version=str(row["retriever_version"]),
            limitations=tuple(_decoded(row.get("limitations_json")) or []),
            error_code=(str(row["error_code"]) if row.get("error_code") is not None else None),
            error_message=(
                str(row["error_message"]) if row.get("error_message") is not None else None
            ),
            created_at=int(row["created_at"]),
            started_at=(int(row["started_at"]) if row.get("started_at") is not None else None),
            completed_at=(
                int(row["completed_at"]) if row.get("completed_at") is not None else None
            ),
        )


__all__ = ["PostgreSQLFollowupRepository"]
