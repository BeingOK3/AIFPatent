from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from .corpus import PatentCorpusIngestService
from .database import Database
from .providers import FetchedDocument


class CorpusMigrationError(RuntimeError):
    """Raised when a historical document cannot be trusted for migration."""


class MigrationStatus(StrEnum):
    ALREADY_READY = "ALREADY_READY"
    LOCAL_READY = "LOCAL_READY"
    MIGRATED_LOCAL = "MIGRATED_LOCAL"
    REHYDRATABLE = "REHYDRATABLE"
    REHYDRATED = "REHYDRATED"
    UNAVAILABLE = "UNAVAILABLE"
    FAILED = "FAILED"


@dataclass(frozen=True)
class HistoricalCorpusCandidate:
    run_id: str
    document_id: str
    publication_number: str
    language: str
    url: str
    deep_reviewed: bool
    row: dict[str, object]


@dataclass(frozen=True)
class MigrationOutcome:
    run_id: str
    document_id: str
    publication_number: str
    status: MigrationStatus
    version_id: str | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class MigrationReport:
    apply: bool
    outcomes: tuple[MigrationOutcome, ...]

    @property
    def counts(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for outcome in self.outcomes:
            result[outcome.status.value] = result.get(outcome.status.value, 0) + 1
        return result


class HistoricalCorpusFetcher(Protocol):
    async def fetch(
        self, candidate: HistoricalCorpusCandidate
    ) -> tuple[FetchedDocument | None, tuple[str, ...]]: ...


class HistoricalRetrievalService(Protocol):
    async def rehydrate_document(
        self,
        *,
        run_id: str,
        publication_number: str,
        url: str = "",
        language: str = "en",
    ) -> tuple[FetchedDocument | None, tuple[str, ...]]: ...


class RetrievalCorpusFetcher:
    """Route migration fetches through the application's retrieval service."""

    def __init__(self, retrieval: HistoricalRetrievalService) -> None:
        self.retrieval = retrieval

    async def fetch(
        self, candidate: HistoricalCorpusCandidate
    ) -> tuple[FetchedDocument | None, tuple[str, ...]]:
        return await self.retrieval.rehydrate_document(
            run_id=candidate.run_id,
            publication_number=candidate.publication_number,
            url=candidate.url,
            language=candidate.language,
        )


class HistoricalCorpusMigrator:
    """Dry-run or rehydrate terminal historical Run documents into the Corpus."""

    _TERMINAL_STATUSES = ("COMPLETED", "COMPLETED_WITH_LIMITATIONS")

    def __init__(
        self,
        *,
        database: Database,
        ingest: PatentCorpusIngestService | None = None,
        fetcher: HistoricalCorpusFetcher | None = None,
    ) -> None:
        self.database = database
        self.ingest = ingest
        self.fetcher = fetcher

    def candidates(
        self, *, run_id: str | None = None, limit: int | None = None
    ) -> tuple[HistoricalCorpusCandidate, ...]:
        if limit is not None and limit <= 0:
            raise ValueError("migration limit must be positive")
        query = """
            SELECT rd.run_id, rd.deep_reviewed AS run_deep_reviewed, d.*
            FROM run_documents AS rd
            JOIN patent_documents AS d ON d.document_id = rd.document_id
            JOIN idea_runs AS r ON r.run_id = rd.run_id
            WHERE r.status IN (?, ?)
              AND rd.deep_reviewed = 1
        """
        parameters: list[object] = list(self._TERMINAL_STATUSES)
        if run_id is not None:
            if not run_id.strip():
                raise ValueError("run_id must not be empty")
            query += " AND rd.run_id = ?"
            parameters.append(run_id)
        query += " ORDER BY r.created_at, rd.run_id, d.publication_number"
        if limit is not None:
            query += " LIMIT ?"
            parameters.append(limit)
        with self.database.connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return tuple(
            HistoricalCorpusCandidate(
                run_id=str(row["run_id"]),
                document_id=str(row["document_id"]),
                publication_number=str(row["publication_number"]),
                language=str(row["language"] or "en"),
                url=str(row["url"] or ""),
                deep_reviewed=bool(row["run_deep_reviewed"]),
                row=dict(row),
            )
            for row in rows
        )

    async def migrate(
        self,
        *,
        apply: bool,
        run_id: str | None = None,
        limit: int | None = None,
    ) -> MigrationReport:
        outcomes = []
        for candidate in self.candidates(run_id=run_id, limit=limit):
            outcomes.append(await self._migrate_one(candidate, apply=apply))
        return MigrationReport(apply=apply, outcomes=tuple(outcomes))

    async def _migrate_one(
        self, candidate: HistoricalCorpusCandidate, *, apply: bool
    ) -> MigrationOutcome:
        try:
            document = self._local_document(candidate)
            if not apply:
                status = (
                    MigrationStatus.LOCAL_READY
                    if document is not None
                    else MigrationStatus.REHYDRATABLE
                    if candidate.publication_number or candidate.url
                    else MigrationStatus.UNAVAILABLE
                )
                return self._outcome(candidate, status)
            if self.ingest is None:
                raise CorpusMigrationError("corpus ingest is required in apply mode")
            existing = await self.ingest.run_links.get(
                candidate.run_id, candidate.document_id
            )
            if existing is not None:
                if existing.corpus_availability == "READY" and existing.version_id:
                    if candidate.deep_reviewed:
                        await self.ingest.mark_deep_reviewed(
                            candidate.run_id, (candidate.document_id,)
                        )
                    return self._outcome(
                        candidate,
                        MigrationStatus.ALREADY_READY,
                        version_id=existing.version_id,
                    )
                return self._outcome(
                    candidate,
                    MigrationStatus.FAILED,
                    error_code="LINK_NOT_READY",
                )
            migrated_status = MigrationStatus.MIGRATED_LOCAL
            failures: tuple[str, ...] = ()
            if document is None:
                if self.fetcher is None:
                    return self._outcome(
                        candidate,
                        MigrationStatus.UNAVAILABLE,
                        error_code="FETCHER_UNAVAILABLE",
                    )
                document, failures = await self.fetcher.fetch(candidate)
                if document is None:
                    return self._outcome(
                        candidate,
                        MigrationStatus.UNAVAILABLE,
                        error_code=failures[-1] if failures else "REHYDRATION_FAILED",
                    )
                migrated_status = MigrationStatus.REHYDRATED
                self._validate_historical_hash(candidate, document)
            if self._identifier(document.publication_number) != self._identifier(
                candidate.publication_number
            ):
                raise CorpusMigrationError("rehydrated publication identity does not match")
            result = await self.ingest.ingest_many(
                run_id=candidate.run_id,
                documents=(document,),
                document_ids={document.publication_number: candidate.document_id},
            )
            if candidate.deep_reviewed:
                await self.ingest.mark_deep_reviewed(
                    candidate.run_id, (candidate.document_id,)
                )
            return self._outcome(
                candidate,
                migrated_status,
                version_id=result.version_ids[0],
            )
        except Exception as exc:
            return self._outcome(
                candidate,
                MigrationStatus.FAILED,
                error_code=getattr(exc, "error_code", type(exc).__name__),
            )

    def _local_document(
        self, candidate: HistoricalCorpusCandidate
    ) -> FetchedDocument | None:
        row = candidate.row
        abstract = str(row.get("abstract_text") or "")
        claims = str(row.get("claims_text") or "")
        description = str(row.get("description_text") or "")
        content = "\n\n".join((abstract, claims, description))
        if not content.strip():
            return None
        content_hash = str(row.get("content_hash") or "")
        if hashlib.sha256(content.encode("utf-8")).hexdigest() != content_hash:
            raise CorpusMigrationError("historical transient text hash mismatch")
        metadata = self._json_object(row.get("metadata_json"))
        spans = metadata.pop("section_spans", {})
        return FetchedDocument(
            provider=str(metadata.get("source") or metadata.get("parser") or "stored"),
            publication_number=candidate.publication_number,
            application_number=self._optional(row.get("application_number")),
            family_id=self._optional(row.get("family_id")),
            title=str(row.get("title") or ""),
            assignee=self._optional(row.get("assignee")),
            inventors=self._json_list(row.get("inventors_json")),
            priority_date=self._optional(row.get("priority_date")),
            filing_date=self._optional(row.get("filing_date")),
            publication_date=self._optional(row.get("publication_date")),
            grant_date=self._optional(row.get("grant_date")),
            language=candidate.language,
            url=candidate.url or "https://patents.google.com",
            abstract_text=abstract,
            claims_text=claims,
            description_text=description,
            section_spans=spans if isinstance(spans, dict) else {},
            raw_metadata=metadata,
        )

    @staticmethod
    def _validate_historical_hash(
        candidate: HistoricalCorpusCandidate, document: FetchedDocument
    ) -> None:
        expected = str(candidate.row.get("content_hash") or "")
        content = "\n\n".join(
            (
                document.abstract_text,
                document.claims_text,
                document.description_text,
            )
        )
        actual = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if not expected or actual != expected:
            raise CorpusMigrationError("rehydrated text does not match historical content hash")

    @staticmethod
    def _json_object(value: object) -> dict[str, object]:
        try:
            parsed = json.loads(str(value or "{}"))
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _json_list(value: object) -> list[str]:
        try:
            parsed = json.loads(str(value or "[]"))
        except json.JSONDecodeError:
            return []
        return [str(item) for item in parsed] if isinstance(parsed, list) else []

    @staticmethod
    def _optional(value: object) -> str | None:
        return None if value is None else str(value)

    @staticmethod
    def _identifier(value: str) -> str:
        return "".join(character for character in value.upper() if character.isalnum())

    @staticmethod
    def _outcome(
        candidate: HistoricalCorpusCandidate,
        status: MigrationStatus,
        *,
        version_id: str | None = None,
        error_code: str | None = None,
    ) -> MigrationOutcome:
        return MigrationOutcome(
            run_id=candidate.run_id,
            document_id=candidate.document_id,
            publication_number=candidate.publication_number,
            status=status,
            version_id=version_id,
            error_code=error_code,
        )


__all__ = [
    "CorpusMigrationError",
    "HistoricalCorpusCandidate",
    "HistoricalCorpusFetcher",
    "HistoricalRetrievalService",
    "HistoricalCorpusMigrator",
    "MigrationOutcome",
    "MigrationReport",
    "MigrationStatus",
    "RetrievalCorpusFetcher",
]
