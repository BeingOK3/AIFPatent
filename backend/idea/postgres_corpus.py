from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from .chunks import PatentChunk
from .corpus import CorpusRunLink, CorpusVersion, CorpusVersionSource
from .lexical import lexical_search_terms
from .ports import Repository


class PostgreSQLCorpusError(RuntimeError):
    """Raised when a PostgreSQL corpus write/read cannot be trusted."""


Connect = Callable[[str], Awaitable[Any]]


class PostgreSQLCorpusVersionRepository(Repository[str, CorpusVersion]):
    """PostgreSQL repository for immutable corpus Versions and Blob metadata."""

    def __init__(self, dsn: str | None = None, *, connect: Connect | None = None) -> None:
        self.dsn = (dsn or os.environ.get("AIFPATENT_POSTGRES_DSN") or "").strip()
        if not self.dsn:
            raise ValueError("PostgreSQL corpus repository requires a DSN")
        self._connect = connect

    async def _connection(self) -> Any:
        if self._connect is not None:
            return await self._connect(self.dsn)
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise PostgreSQLCorpusError("psycopg is required for PostgreSQL corpus storage") from exc
        return await psycopg.AsyncConnection.connect(self.dsn)

    @staticmethod
    def _millis(value: datetime) -> int:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return int(value.timestamp() * 1000)

    @staticmethod
    def _datetime(value: Any) -> datetime:
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)

    @staticmethod
    def _row_to_version(row: Any) -> CorpusVersion:
        metadata = row["metadata_json"] or {}
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        provider = str(metadata.get("provider") or "postgresql")
        return CorpusVersion(
            version_id=str(row["version_id"]),
            publication_number=str(row["publication_number"]),
            language=str(row["language"]),
            provider=provider,
            object_key=str(row["object_key"]),
            content_sha256=str(row["normalized_content_hash"]),
            normalized_size=int(row["uncompressed_bytes"]),
            created_at=PostgreSQLCorpusVersionRepository._datetime(row["created_at"]),
            status=str(row["state"]),
            document_id=str(row["document_id"]),
        )

    @staticmethod
    def _version_row_mapping(row: Any) -> dict[str, Any]:
        if isinstance(row, dict):
            return row
        return dict(
            zip(
                (
                    "version_id",
                    "document_id",
                    "publication_number",
                    "language",
                    "normalized_content_hash",
                    "state",
                    "metadata_json",
                    "created_at",
                    "object_key",
                    "uncompressed_bytes",
                ),
                row,
                strict=True,
            )
        )

    async def get(self, key: str) -> CorpusVersion | None:
        connection = await self._connection()
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT pv.version_id, pv.document_id, pd.publication_number,
                           pv.language, pv.normalized_content_hash, pv.state,
                           pv.metadata_json, pv.created_at, cb.object_key,
                           cb.uncompressed_bytes
                    FROM patent_document_versions AS pv
                    JOIN patent_documents AS pd ON pd.document_id = pv.document_id
                    JOIN corpus_blobs AS cb ON cb.blob_hash = pv.normalized_blob_hash
                    WHERE pv.version_id = %s
                    """,
                    (key,),
                )
                row = await cursor.fetchone()
            return None if row is None else self._row_to_version(self._version_row_mapping(row))
        finally:
            await connection.close()

    async def put_if_absent(self, key: str, entity: CorpusVersion) -> bool:
        if key != entity.version_id:
            raise PostgreSQLCorpusError("repository key does not match Version ID")
        connection = await self._connection()
        try:
            async with connection.cursor() as cursor:
                if entity.document_id:
                    await cursor.execute(
                        "SELECT document_id FROM patent_documents WHERE document_id = %s",
                        (entity.document_id,),
                    )
                else:
                    await cursor.execute(
                        """
                        SELECT document_id FROM patent_documents
                        WHERE publication_number = %s AND COALESCE(language, '') = %s
                        ORDER BY updated_at DESC LIMIT 1
                        """,
                        (entity.publication_number, entity.language),
                    )
                document_row = await cursor.fetchone()
                if document_row is None:
                    raise PostgreSQLCorpusError("patent document must exist before Version persistence")
                document_id = str(document_row["document_id"] if isinstance(document_row, dict) else document_row[0])
                metadata = json.dumps(
                    {"provider": entity.provider}, ensure_ascii=True, sort_keys=True, separators=(",", ":")
                )
                await cursor.execute(
                    """
                    INSERT INTO corpus_blobs(
                        blob_hash, encoding, object_key, uncompressed_bytes,
                        compressed_bytes, content_type, state, created_at, verified_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, 'READY', %s, %s)
                    ON CONFLICT (blob_hash) DO NOTHING
                    """,
                    (
                        entity.content_sha256,
                        "identity",
                        entity.object_key,
                        entity.normalized_size,
                        entity.normalized_size,
                        "application/json",
                        self._millis(entity.created_at),
                        self._millis(entity.created_at),
                    ),
                )
                await cursor.execute(
                    """
                    INSERT INTO patent_document_versions(
                        version_id, document_id, language, normalized_content_hash,
                        normalized_blob_hash, parser_version, schema_version,
                        state, metadata_json, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                    ON CONFLICT (version_id) DO NOTHING
                    """,
                    (
                        entity.version_id,
                        document_id,
                        entity.language,
                        entity.content_sha256,
                        entity.content_sha256,
                        "aifpatent-corpus/1",
                        "aifpatent-corpus/1",
                        entity.status,
                        metadata,
                        self._millis(entity.created_at),
                    ),
                )
                await connection.commit()
                return cursor.rowcount == 1
        except PostgreSQLCorpusError:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def healthcheck(self) -> dict[str, Any]:
        connection = await self._connection()
        try:
            async with connection.cursor() as cursor:
                await cursor.execute("SELECT 1")
                row = await cursor.fetchone()
            return {"ok": row is not None}
        finally:
            await connection.close()


class PostgreSQLCorpusPrerequisiteRepository:
    """Bridge current SQLite run/document identities into PostgreSQL before Corpus writes."""

    def __init__(
        self,
        database: Any,
        dsn: str | None = None,
        *,
        connect: Connect | None = None,
    ) -> None:
        self.database = database
        self.dsn = (dsn or os.environ.get("AIFPATENT_POSTGRES_DSN") or "").strip()
        if not self.dsn:
            raise ValueError("PostgreSQL corpus prerequisite repository requires a DSN")
        self._connect = connect

    async def _connection(self) -> Any:
        if self._connect is not None:
            return await self._connect(self.dsn)
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise PostgreSQLCorpusError("psycopg is required for PostgreSQL corpus storage") from exc
        return await psycopg.AsyncConnection.connect(self.dsn)

    def _source_rows(
        self, run_id: str, document_ids: tuple[str, ...]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        run_chain: list[dict[str, Any]] = []
        current = self.database.get_run(run_id)
        while True:
            run_chain.append(current)
            parent_run_id = current.get("parent_run_id")
            if not parent_run_id:
                break
            current = self.database.get_run(str(parent_run_id))
        run_chain.reverse()

        placeholders = ",".join("?" for _ in document_ids)
        with self.database.connect() as connection:
            case_ids = tuple(dict.fromkeys(str(run["case_id"]) for run in run_chain))
            case_placeholders = ",".join("?" for _ in case_ids)
            cases = [
                dict(row)
                for row in connection.execute(
                    f"SELECT * FROM idea_cases WHERE case_id IN ({case_placeholders})",
                    case_ids,
                ).fetchall()
            ]
            documents = [
                dict(row)
                for row in connection.execute(
                    f"SELECT * FROM patent_documents WHERE document_id IN ({placeholders})",
                    document_ids,
                ).fetchall()
            ]
            run_documents = [
                dict(row)
                for row in connection.execute(
                    f"""
                    SELECT * FROM run_documents
                    WHERE run_id = ? AND document_id IN ({placeholders})
                    """,
                    (run_id, *document_ids),
                ).fetchall()
            ]
        if len(documents) != len(document_ids) or len(run_documents) != len(document_ids):
            raise PostgreSQLCorpusError(
                "SQLite run/document prerequisites are incomplete for Corpus persistence"
            )
        return cases, run_chain, documents, run_documents

    async def prepare(self, run_id: str, document_ids: tuple[str, ...]) -> None:
        if not document_ids or len(set(document_ids)) != len(document_ids):
            raise PostgreSQLCorpusError("Corpus prerequisites require unique document IDs")
        cases, runs, documents, run_documents = self._source_rows(run_id, document_ids)
        connection = await self._connection()
        try:
            async with connection.cursor() as cursor:
                for case in cases:
                    await cursor.execute(
                        """
                        INSERT INTO idea_cases(case_id, title, created_at, updated_at, archived_at)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (case_id) DO NOTHING
                        """,
                        (
                            case["case_id"],
                            case["title"],
                            case["created_at"],
                            case["updated_at"],
                            case["archived_at"],
                        ),
                    )
                for run in runs:
                    await cursor.execute(
                        """
                        INSERT INTO idea_runs(
                            run_id, case_id, parent_run_id, status, evaluation_date,
                            date_basis, analysis_scope, model, skill_version,
                            workflow_version, config_snapshot, limitation_json,
                            created_at, started_at, completed_at, error_code, error_message
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s
                        )
                        ON CONFLICT (run_id) DO UPDATE SET
                            status = EXCLUDED.status,
                            limitation_json = EXCLUDED.limitation_json,
                            started_at = EXCLUDED.started_at,
                            completed_at = EXCLUDED.completed_at,
                            error_code = EXCLUDED.error_code,
                            error_message = EXCLUDED.error_message
                        """,
                        (
                            run["run_id"], run["case_id"], run["parent_run_id"], run["status"],
                            run["evaluation_date"], run["date_basis"], run["analysis_scope"],
                            run["model"], run["skill_version"], run["workflow_version"],
                            json.dumps(run["config_snapshot"], ensure_ascii=False),
                            json.dumps(run["limitation_json"], ensure_ascii=False),
                            run["created_at"], run["started_at"], run["completed_at"],
                            run["error_code"], run["error_message"],
                        ),
                    )
                    await cursor.execute(
                        """
                        INSERT INTO run_inputs(
                            run_id, input_text, input_hash, attachments_json, settings_json
                        ) VALUES (%s, %s, %s, %s::jsonb, %s::jsonb)
                        ON CONFLICT (run_id) DO NOTHING
                        """,
                        (
                            run["run_id"], run["input_text"], run["input_hash"],
                            json.dumps(run["attachments_json"], ensure_ascii=False),
                            json.dumps(run["settings_json"], ensure_ascii=False),
                        ),
                    )
                for document in documents:
                    if document["family_id"]:
                        await cursor.execute(
                            """
                            INSERT INTO patent_families(family_id, source)
                            VALUES (%s, %s) ON CONFLICT (family_id) DO NOTHING
                            """,
                            (document["family_id"], "sqlite-corpus-bridge"),
                        )
                    await cursor.execute(
                        """
                        INSERT INTO patent_documents(
                            document_id, publication_number, application_number, family_id,
                            title, assignee, inventors_json, priority_date, filing_date,
                            publication_date, grant_date, language, url, abstract_text,
                            claims_text, description_text, content_hash, metadata_json,
                            created_at, updated_at
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s
                        ) ON CONFLICT (document_id) DO NOTHING
                        """,
                        (
                            document["document_id"], document["publication_number"],
                            document["application_number"], document["family_id"],
                            document["title"], document["assignee"], document["inventors_json"],
                            document["priority_date"], document["filing_date"],
                            document["publication_date"], document["grant_date"],
                            document["language"], document["url"], None, None, None,
                            document["content_hash"], document["metadata_json"],
                            document["created_at"], document["updated_at"],
                        ),
                    )
                for item in run_documents:
                    await cursor.execute(
                        """
                        INSERT INTO run_documents(
                            run_id, document_id, relevance, relevance_score,
                            screening_status, deep_reviewed, found_by_json, query_ids_json
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                        ON CONFLICT (run_id, document_id) DO NOTHING
                        """,
                        (
                            item["run_id"], item["document_id"], item["relevance"],
                            item["relevance_score"], item["screening_status"],
                            bool(item["deep_reviewed"]), item["found_by_json"],
                            item["query_ids_json"],
                        ),
                    )
                await connection.commit()
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()


class PostgreSQLCorpusRunLinkRepository:
    """Write-once PostgreSQL binding from an IDEA run document to a Version."""

    def __init__(self, dsn: str | None = None, *, connect: Connect | None = None) -> None:
        self.dsn = (dsn or os.environ.get("AIFPATENT_POSTGRES_DSN") or "").strip()
        if not self.dsn:
            raise ValueError("PostgreSQL corpus run-link repository requires a DSN")
        self._connect = connect

    async def _connection(self) -> Any:
        if self._connect is not None:
            return await self._connect(self.dsn)
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise PostgreSQLCorpusError("psycopg is required for PostgreSQL corpus storage") from exc
        return await psycopg.AsyncConnection.connect(self.dsn)

    @staticmethod
    def _row_to_link(row: Any) -> CorpusRunLink:
        if not isinstance(row, dict):
            row = dict(
                zip(
                    (
                        "run_id",
                        "document_id",
                        "version_id",
                        "corpus_availability",
                        "deep_reviewed",
                        "linked_at",
                    ),
                    row,
                    strict=True,
                )
            )
        return CorpusRunLink(
            run_id=str(row["run_id"]),
            document_id=str(row["document_id"]),
            version_id=str(row["version_id"]),
            corpus_availability=str(row["corpus_availability"]),
            deep_reviewed=bool(row["deep_reviewed"]),
            linked_at=PostgreSQLCorpusVersionRepository._datetime(row["linked_at"]),
        )

    async def get(self, run_id: str, document_id: str) -> CorpusRunLink | None:
        connection = await self._connection()
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT run_id, document_id, version_id, corpus_availability,
                           deep_reviewed, linked_at
                    FROM run_document_versions
                    WHERE run_id = %s AND document_id = %s
                    """,
                    (run_id, document_id),
                )
                row = await cursor.fetchone()
            return None if row is None else self._row_to_link(row)
        finally:
            await connection.close()

    async def put_if_absent(self, link: CorpusRunLink) -> bool:
        if link.corpus_availability != "READY":
            raise PostgreSQLCorpusError("new run corpus links must reference a READY Version")
        linked_at = link.linked_at or datetime.now(timezone.utc)
        connection = await self._connection()
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO run_document_versions(
                        run_id, document_id, version_id, corpus_availability,
                        deep_reviewed, linked_at
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (run_id, document_id) DO NOTHING
                    RETURNING run_id
                    """,
                    (
                        link.run_id,
                        link.document_id,
                        link.version_id,
                        link.corpus_availability,
                        link.deep_reviewed,
                        PostgreSQLCorpusVersionRepository._millis(linked_at),
                    ),
                )
                inserted = await cursor.fetchone()
                await connection.commit()
                return inserted is not None
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def mark_deep_reviewed(
        self, run_id: str, document_ids: tuple[str, ...]
    ) -> None:
        if not document_ids or len(set(document_ids)) != len(document_ids):
            raise PostgreSQLCorpusError("deep-review update requires unique document IDs")
        connection = await self._connection()
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    UPDATE run_document_versions
                    SET deep_reviewed = TRUE
                    WHERE run_id = %s AND document_id = ANY(%s)
                          AND corpus_availability = 'READY'
                    RETURNING document_id
                    """,
                    (run_id, list(document_ids)),
                )
                updated = {str(row[0]) for row in await cursor.fetchall()}
                if updated != set(document_ids):
                    raise PostgreSQLCorpusError(
                        "cannot mark missing or unavailable run corpus links as deep reviewed"
                    )
                await cursor.execute(
                    """
                    UPDATE run_documents
                    SET deep_reviewed = TRUE
                    WHERE run_id = %s AND document_id = ANY(%s)
                    """,
                    (run_id, list(document_ids)),
                )
                if cursor.rowcount != len(document_ids):
                    raise PostgreSQLCorpusError(
                        "cannot synchronize deep-review state to run documents"
                    )
                await connection.commit()
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()


class PostgreSQLCorpusVersionSourceRepository:
    """Append-only provenance records kept outside stable content identity."""

    def __init__(self, dsn: str | None = None, *, connect: Connect | None = None) -> None:
        self.dsn = (dsn or os.environ.get("AIFPATENT_POSTGRES_DSN") or "").strip()
        if not self.dsn:
            raise ValueError("PostgreSQL corpus source repository requires a DSN")
        self._connect = connect

    async def _connection(self) -> Any:
        if self._connect is not None:
            return await self._connect(self.dsn)
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise PostgreSQLCorpusError("psycopg is required for PostgreSQL corpus storage") from exc
        return await psycopg.AsyncConnection.connect(self.dsn)

    async def put_if_absent(self, source: CorpusVersionSource) -> bool:
        connection = await self._connection()
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO patent_version_sources(
                        source_id, version_id, provider, source_url, retrieved_at,
                        raw_response_hash, parser_version, metadata_json
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                    ON CONFLICT (source_id) DO NOTHING
                    RETURNING source_id
                    """,
                    (
                        source.source_id,
                        source.version_id,
                        source.provider,
                        source.source_url,
                        PostgreSQLCorpusVersionRepository._millis(source.retrieved_at),
                        source.raw_response_hash,
                        source.parser_version,
                        json.dumps(
                            source.metadata,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    ),
                )
                inserted = await cursor.fetchone()
                await connection.commit()
                return inserted is not None
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def healthcheck(self) -> dict[str, Any]:
        connection = await self._connection()
        try:
            async with connection.cursor() as cursor:
                await cursor.execute("SELECT 1")
                row = await cursor.fetchone()
            return {"ok": row is not None}
        finally:
            await connection.close()


class PostgreSQLPatentChunkRepository:
    """Idempotent PostgreSQL persistence for deterministic patent Chunks."""

    _FIELDS = (
        "chunk_id",
        "version_id",
        "publication_number",
        "section_type",
        "section_label",
        "claim_number",
        "claim_kind",
        "parent_claims_json",
        "start_offset",
        "end_offset",
        "text",
        "text_hash",
        "token_count",
        "chunker_version",
    )

    def __init__(self, dsn: str | None = None, *, connect: Connect | None = None) -> None:
        self.dsn = (dsn or os.environ.get("AIFPATENT_POSTGRES_DSN") or "").strip()
        if not self.dsn:
            raise ValueError("PostgreSQL patent Chunk repository requires a DSN")
        self._connect = connect

    async def _connection(self) -> Any:
        if self._connect is not None:
            return await self._connect(self.dsn)
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise PostgreSQLCorpusError("psycopg is required for PostgreSQL corpus storage") from exc
        return await psycopg.AsyncConnection.connect(self.dsn)

    @classmethod
    def _row_to_chunk(cls, row: Any) -> PatentChunk:
        values = row if isinstance(row, dict) else dict(zip(cls._FIELDS, row, strict=True))
        parents = values["parent_claims_json"] or []
        if isinstance(parents, str):
            parents = json.loads(parents)
        return PatentChunk(
            chunk_id=str(values["chunk_id"]),
            version_id=str(values["version_id"]),
            publication_number=str(values["publication_number"]),
            section_type=str(values["section_type"]),
            section_label=str(values["section_label"]),
            claim_number=(
                None if values["claim_number"] is None else int(values["claim_number"])
            ),
            claim_kind=(None if values["claim_kind"] is None else str(values["claim_kind"])),
            parent_claim_numbers=tuple(int(value) for value in parents),
            start_offset=int(values["start_offset"]),
            end_offset=int(values["end_offset"]),
            text=str(values["text"]),
            text_hash=str(values["text_hash"]),
            token_count=int(values["token_count"]),
            chunker_version=str(values["chunker_version"]),
        )

    async def put_many_if_absent(
        self, chunks: tuple[PatentChunk, ...]
    ) -> tuple[PatentChunk, ...]:
        if not chunks:
            raise PostgreSQLCorpusError("cannot persist an empty Chunk set")
        if len({chunk.chunk_id for chunk in chunks}) != len(chunks):
            raise PostgreSQLCorpusError("Chunk IDs must be unique")
        if len({chunk.version_id for chunk in chunks}) != 1:
            raise PostgreSQLCorpusError("one write must contain a single Version")
        if len({chunk.chunker_version for chunk in chunks}) != 1:
            raise PostgreSQLCorpusError("one write must contain a single Chunker version")
        if len({chunk.publication_number for chunk in chunks}) != 1:
            raise PostgreSQLCorpusError("one write must contain a single publication")

        connection = await self._connection()
        try:
            async with connection.cursor() as cursor:
                created_at = PostgreSQLCorpusVersionRepository._millis(datetime.now(timezone.utc))
                for chunk in chunks:
                    terms = lexical_search_terms(
                        text=chunk.text,
                        publication_number=chunk.publication_number,
                        section_type=chunk.section_type,
                        section_label=chunk.section_label,
                    )
                    await cursor.execute(
                        """
                        INSERT INTO patent_chunks(
                            chunk_id, version_id, publication_number, section_type,
                            section_label, claim_number, claim_kind, parent_claims_json,
                            start_offset, end_offset, text, text_hash, token_count,
                            chunker_version, search_terms, metadata_json, created_at
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s::jsonb,
                            %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s
                        ) ON CONFLICT (chunk_id) DO NOTHING
                        """,
                        (
                            chunk.chunk_id,
                            chunk.version_id,
                            chunk.publication_number,
                            chunk.section_type,
                            chunk.section_label,
                            chunk.claim_number,
                            chunk.claim_kind,
                            json.dumps(chunk.parent_claim_numbers),
                            chunk.start_offset,
                            chunk.end_offset,
                            chunk.text,
                            chunk.text_hash,
                            chunk.token_count,
                            chunk.chunker_version,
                            terms.value,
                            json.dumps({"lexical_tokenizer_version": terms.version}),
                            created_at,
                        ),
                    )
                await cursor.execute(
                    """
                    SELECT chunk_id, version_id, publication_number, section_type,
                           section_label, claim_number, claim_kind, parent_claims_json,
                           start_offset, end_offset, text, text_hash, token_count,
                           chunker_version
                    FROM patent_chunks
                    WHERE version_id = %s AND chunker_version = %s
                    """,
                    (chunks[0].version_id, chunks[0].chunker_version),
                )
                persisted = {
                    item.chunk_id: item
                    for item in (self._row_to_chunk(row) for row in await cursor.fetchall())
                }
                expected = {chunk.chunk_id: chunk for chunk in chunks}
                if persisted != expected:
                    raise PostgreSQLCorpusError(
                        "persisted Chunk rows conflict with deterministic output"
                    )
                await connection.commit()
                return tuple(persisted[chunk.chunk_id] for chunk in chunks)
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()

    async def healthcheck(self) -> dict[str, Any]:
        connection = await self._connection()
        try:
            async with connection.cursor() as cursor:
                await cursor.execute("SELECT 1")
                row = await cursor.fetchone()
            return {"ok": row is not None}
        finally:
            await connection.close()


__all__ = [
    "PostgreSQLCorpusError",
    "PostgreSQLCorpusPrerequisiteRepository",
    "PostgreSQLCorpusRunLinkRepository",
    "PostgreSQLCorpusVersionSourceRepository",
    "PostgreSQLCorpusVersionRepository",
    "PostgreSQLPatentChunkRepository",
]
