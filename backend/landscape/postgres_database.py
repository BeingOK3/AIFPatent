from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from typing import Any

from idea.postgres_database import PostgreSQLPersistenceError, _Connection
from idea.merge import normalize_publication_number
from idea.providers.base import FetchedDocument

from .company_assignment import CompanyAssignmentResult
from .database import LandscapeDatabase, assert_no_secrets, canonical_json, now_ms
from .schemas import (
    CompanyAssignment,
    LandscapePatentAnalysis,
    LandscapeScope,
    NormalizedCompany,
)


def _prepare_candidate_rows(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate and canonicalize the complete eligible candidate set."""

    prepared: list[dict[str, Any]] = []
    identities: set[tuple[str, str]] = set()
    ranks: set[int] = set()
    for candidate in candidates:
        metadata = candidate.get("metadata", {})
        assert_no_secrets(metadata)
        publication_number = str(candidate["publication_number"]).strip().upper()
        normalized = normalize_publication_number(publication_number)
        normalized_key = str(candidate["normalized_key"]).strip().upper()
        if not normalized or publication_number != normalized or normalized_key != normalized:
            raise ValueError(
                "candidate publication_number and normalized_key must be the canonical publication number"
            )
        document_id = str(candidate["document_id"]).strip()
        if not document_id:
            raise ValueError("candidate document_id cannot be blank")
        rank = candidate["rank"]
        if isinstance(rank, bool) or not isinstance(rank, int) or rank < 1:
            raise ValueError("candidate rank must be a positive integer")
        decision = str(candidate.get("decision", "ELIGIBLE")).strip().upper()
        if decision != "ELIGIBLE":
            raise ValueError("canonical candidates must have decision ELIGIBLE")
        if (document_id, normalized) in identities or rank in ranks:
            raise ValueError("candidate document, publication, normalized key and rank must be unique")
        if any(
            row["document_id"] == document_id
            or row["publication_number"] == normalized
            for row in prepared
        ):
            raise ValueError("candidate document, publication, normalized key and rank must be unique")
        identities.add((document_id, normalized))
        ranks.add(rank)
        payload = {
            "document_id": document_id,
            "publication_number": normalized,
            "normalized_key": normalized,
            "rank": rank,
            "decision": decision,
            "metadata": metadata,
        }
        encoded = canonical_json(payload)
        prepared.append(
            {
                **payload,
                "metadata_json": canonical_json(metadata),
                "content_hash": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
            }
        )
    if ranks and ranks != set(range(1, len(prepared) + 1)):
        raise ValueError("candidate ranks must be contiguous and start at 1")
    return sorted(prepared, key=lambda item: (item["rank"], item["publication_number"]))


def _candidate_comparison_value(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row["metadata_json"]
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    return {
        "document_id": row["document_id"],
        "publication_number": row["publication_number"],
        "normalized_key": row["normalized_key"],
        "rank": row["rank"],
        "decision": row["decision"],
        "metadata": metadata,
        "metadata_json": canonical_json(metadata),
        "content_hash": row["content_hash"],
    }


def _prepare_company_assignment_rows(
    scope: LandscapeScope,
    result: CompanyAssignmentResult,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    """Map deterministic domain assignments to stable PostgreSQL rows."""

    assignments_by_company: dict[str, list[CompanyAssignment]] = {}
    for assignment in result.assignments:
        if assignment.co_assignees:
            raise ValueError(
                "co-assignees cannot be persisted until company identities are explicit"
            )
        assignments_by_company.setdefault(
            assignment.primary_company_id, []
        ).append(assignment)

    company_rows: list[dict[str, Any]] = []
    company_ids: set[str] = set()
    for company in result.companies:
        if company.company_id in company_ids:
            raise ValueError("company IDs must be unique")
        company_ids.add(company.company_id)
        if company.company_id == "UNKNOWN":
            resolution_source = "UNRESOLVED"
            confidence = 0.0
        elif scope.competitors:
            resolution_source = "USER_CONFIRMED_REGISTRY"
            confidence = 1.0
        else:
            resolution_source = "NORMALIZED_OBSERVED_NAME"
            confidence = 1.0
        raw_names = sorted(
            {
                " ".join(assignment.observed_assignee.split())
                for assignment in assignments_by_company.get(company.company_id, [])
                if assignment.observed_assignee
            },
            key=lambda value: (value.casefold(), value),
        )
        semantic = {
            "company_id": company.company_id,
            "canonical_name": company.canonical_name,
            "aliases": company.aliases,
            "raw_names": raw_names,
            "resolution_source": resolution_source,
            "confidence": confidence,
        }
        company_rows.append(
            {
                **semantic,
                "aliases_json": canonical_json(company.aliases),
                "raw_names_json": canonical_json(raw_names),
                "content_hash": hashlib.sha256(
                    canonical_json(semantic).encode("utf-8")
                ).hexdigest(),
            }
        )

    assignment_rows: list[dict[str, Any]] = []
    publications: set[str] = set()
    for assignment in result.assignments:
        publication = normalize_publication_number(assignment.publication_number)
        if publication is None or publication != assignment.publication_number:
            raise ValueError("assignment publication number must be canonical")
        if publication in publications:
            raise ValueError("each publication must have exactly one primary assignment")
        publications.add(publication)
        if assignment.primary_company_id not in company_ids:
            raise ValueError("assignment references an unknown company")
        confidence = (
            1.0
            if assignment.status in {"CONFIRMED_ALIAS", "NORMALIZED_NAME"}
            else 0.0
        )
        semantic = {
            "publication_number": publication,
            "company_id": assignment.primary_company_id,
            "relationship": "PRIMARY",
            "observed_assignee": assignment.observed_assignee,
            "matched_alias": assignment.matched_alias,
            "assignment_status": assignment.status,
            "confidence": confidence,
        }
        assignment_rows.append(
            {
                **semantic,
                "content_hash": hashlib.sha256(
                    canonical_json(semantic).encode("utf-8")
                ).hexdigest(),
            }
        )

    company_rows.sort(key=lambda row: row["company_id"])
    assignment_rows.sort(key=lambda row: row["publication_number"])
    manifest_hash = hashlib.sha256(
        canonical_json(
            {
                "companies": [
                    _company_projection(row) for row in company_rows
                ],
                "assignments": [
                    _assignment_projection(row) for row in assignment_rows
                ],
            }
        ).encode("utf-8")
    ).hexdigest()
    return company_rows, assignment_rows, manifest_hash


class LandscapePostgreSQLDatabase(LandscapeDatabase):
    """Landscape repository bound to the same PostgreSQL database as IDEA."""

    def __init__(self, dsn: str):
        if not dsn.strip():
            raise ValueError("PostgreSQL DSN must not be blank")
        self.dsn = dsn.strip()
        self.path = "<postgresql>"

    def initialize(self) -> None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT version FROM aifpatent_schema_migrations
                WHERE version = '072_landscape_document_fetches'
                """
            ).fetchone()
        if row is None:
            raise PostgreSQLPersistenceError(
                "PostgreSQL schema is not current; apply migration "
                "072_landscape_document_fetches"
            )

    @contextmanager
    def connect(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover
            raise PostgreSQLPersistenceError("psycopg is required for PostgreSQL persistence") from exc
        raw = psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=10)
        connection = _Connection(raw)
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def table_names(self) -> set[str]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT table_name AS name
                FROM information_schema.tables
                WHERE table_schema = current_schema()
                """
            ).fetchall()
        return {row["name"] for row in rows}

    def put_candidates(
        self, run_id: str, candidates: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Persist the authoritative eligible set once, atomically and idempotently."""

        prepared = _prepare_candidate_rows(candidates)
        with self.connect() as connection:
            existing_rows = connection.execute(
                """
                SELECT document_id,publication_number,normalized_key,rank,decision,
                       metadata_json,content_hash
                FROM landscape_candidates
                WHERE run_id = %s
                ORDER BY rank,publication_number
                """,
                (run_id,),
            ).fetchall()
            if existing_rows:
                existing = [_candidate_comparison_value(dict(row)) for row in existing_rows]
                if canonical_json(existing) != canonical_json(prepared):
                    raise ValueError("landscape canonical candidate set is immutable")
            else:
                for candidate in prepared:
                    connection.execute(
                        """
                        INSERT INTO landscape_candidates(
                            run_id,document_id,publication_number,normalized_key,rank,
                            decision,metadata_json,content_hash,created_at
                        ) VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)
                        """,
                        (
                            run_id,
                            candidate["document_id"],
                            candidate["publication_number"],
                            candidate["normalized_key"],
                            candidate["rank"],
                            candidate["decision"],
                            candidate["metadata_json"],
                            candidate["content_hash"],
                            now_ms(),
                        ),
                    )
        return self.list_candidates(run_id)

    def list_candidates(self, run_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT document_id,publication_number,normalized_key,rank,decision,
                       metadata_json,content_hash,created_at
                FROM landscape_candidates
                WHERE run_id = %s
                ORDER BY rank,publication_number
                """,
                (run_id,),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for raw in rows:
            row = dict(raw)
            metadata = row.pop("metadata_json")
            row["metadata"] = json.loads(metadata) if isinstance(metadata, str) else metadata
            result.append(row)
        return result

    def put_company_assignments(
        self,
        run_id: str,
        *,
        scope: LandscapeScope,
        result: CompanyAssignmentResult,
    ) -> CompanyAssignmentResult:
        """Atomically freeze a complete company registry and PRIMARY partition."""

        company_rows, assignment_rows, manifest_hash = (
            _prepare_company_assignment_rows(scope, result)
        )
        with self.connect() as connection:
            run = connection.execute(
                "SELECT run_id FROM landscape_runs WHERE run_id = %s FOR UPDATE",
                (run_id,),
            ).fetchone()
            if run is None:
                raise ValueError(f"unknown landscape run: {run_id}")
            candidate_rows = connection.execute(
                """
                SELECT document_id,publication_number
                FROM landscape_candidates
                WHERE run_id = %s
                ORDER BY rank,publication_number
                """,
                (run_id,),
            ).fetchall()
            document_by_publication = {
                row["publication_number"]: row["document_id"]
                for row in candidate_rows
            }
            assigned_publications = {
                row["publication_number"] for row in assignment_rows
            }
            if assigned_publications != set(document_by_publication):
                missing = sorted(set(document_by_publication) - assigned_publications)
                invented = sorted(assigned_publications - set(document_by_publication))
                raise ValueError(
                    "company assignments must cover canonical candidates exactly "
                    f"(missing={missing}, outside_u={invented})"
                )

            manifest = connection.execute(
                """
                SELECT company_count,assignment_count,content_hash
                FROM landscape_company_assignment_manifests
                WHERE run_id = %s
                """,
                (run_id,),
            ).fetchone()
            existing_companies = connection.execute(
                """
                SELECT company_id,canonical_name,aliases_json,raw_names_json,
                       resolution_source,confidence,content_hash
                FROM landscape_companies
                WHERE run_id = %s
                ORDER BY company_id
                """,
                (run_id,),
            ).fetchall()
            existing_assignments = connection.execute(
                """
                SELECT c.publication_number,dc.company_id,dc.relationship,
                       dc.observed_assignee,dc.matched_alias,dc.assignment_status,
                       dc.confidence,dc.content_hash
                FROM landscape_document_companies dc
                JOIN landscape_candidates c
                  ON c.run_id = dc.run_id AND c.document_id = dc.document_id
                WHERE dc.run_id = %s
                ORDER BY c.publication_number
                """,
                (run_id,),
            ).fetchall()

            expected_companies = [_company_projection(row) for row in company_rows]
            expected_assignments = [
                _assignment_projection(row) for row in assignment_rows
            ]
            stored_companies = [
                _company_projection(dict(row)) for row in existing_companies
            ]
            stored_assignments = [
                _assignment_projection(dict(row)) for row in existing_assignments
            ]
            if manifest is not None:
                same_manifest = (
                    manifest["company_count"] == len(company_rows)
                    and manifest["assignment_count"] == len(assignment_rows)
                    and manifest["content_hash"] == manifest_hash
                )
                if not (
                    same_manifest
                    and stored_companies == expected_companies
                    and stored_assignments == expected_assignments
                ):
                    raise ValueError(
                        "landscape company assignment set is immutable or corrupt"
                    )
                return result
            if existing_companies or existing_assignments:
                raise ValueError(
                    "landscape company assignment set is partial or corrupt"
                )

            created_at = now_ms()
            for company in company_rows:
                connection.execute(
                    """
                    INSERT INTO landscape_companies(
                        run_id,company_id,canonical_name,aliases_json,raw_names_json,
                        resolution_source,confidence,content_hash,created_at
                    ) VALUES(%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s)
                    """,
                    (
                        run_id,
                        company["company_id"],
                        company["canonical_name"],
                        company["aliases_json"],
                        company["raw_names_json"],
                        company["resolution_source"],
                        company["confidence"],
                        company["content_hash"],
                        created_at,
                    ),
                )
            for assignment in assignment_rows:
                connection.execute(
                    """
                    INSERT INTO landscape_document_companies(
                        run_id,document_id,company_id,relationship,observed_assignee,
                        matched_alias,assignment_status,confidence,content_hash,created_at
                    ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        run_id,
                        document_by_publication[assignment["publication_number"]],
                        assignment["company_id"],
                        assignment["relationship"],
                        assignment["observed_assignee"],
                        assignment["matched_alias"],
                        assignment["assignment_status"],
                        assignment["confidence"],
                        assignment["content_hash"],
                        created_at,
                    ),
                )
            connection.execute(
                """
                INSERT INTO landscape_company_assignment_manifests(
                    run_id,company_count,assignment_count,content_hash,created_at
                ) VALUES(%s,%s,%s,%s,%s)
                """,
                (
                    run_id,
                    len(company_rows),
                    len(assignment_rows),
                    manifest_hash,
                    created_at,
                ),
            )
        return result

    def list_company_assignments(self, run_id: str) -> CompanyAssignmentResult:
        """Read the frozen company partition; absence is not an empty result."""

        with self.connect() as connection:
            manifest = connection.execute(
                """
                SELECT company_count,assignment_count,content_hash
                FROM landscape_company_assignment_manifests
                WHERE run_id = %s
                """,
                (run_id,),
            ).fetchone()
            if manifest is None:
                raise ValueError(
                    f"company assignments have not been persisted for run: {run_id}"
                )
            company_rows = connection.execute(
                """
                SELECT company_id,canonical_name,aliases_json,raw_names_json,
                       resolution_source,confidence,content_hash
                FROM landscape_companies
                WHERE run_id = %s
                ORDER BY company_id
                """,
                (run_id,),
            ).fetchall()
            assignment_rows = connection.execute(
                """
                SELECT c.publication_number,dc.company_id,dc.relationship,
                       dc.observed_assignee,dc.matched_alias,dc.assignment_status,
                       dc.confidence,dc.content_hash
                FROM landscape_document_companies dc
                JOIN landscape_candidates c
                  ON c.run_id = dc.run_id AND c.document_id = dc.document_id
                WHERE dc.run_id = %s
                ORDER BY c.publication_number
                """,
                (run_id,),
            ).fetchall()
        if (
            manifest["company_count"] != len(company_rows)
            or manifest["assignment_count"] != len(assignment_rows)
        ):
            raise ValueError("landscape company assignment set is corrupt")
        stored_hash = hashlib.sha256(
            canonical_json(
                {
                    "companies": [
                        _company_projection(dict(row)) for row in company_rows
                    ],
                    "assignments": [
                        _assignment_projection(dict(row))
                        for row in assignment_rows
                    ],
                }
            ).encode("utf-8")
        ).hexdigest()
        if stored_hash != manifest["content_hash"]:
            raise ValueError("landscape company assignment set is corrupt")
        companies = tuple(
            NormalizedCompany(
                company_id=row["company_id"],
                canonical_name=row["canonical_name"],
                aliases=_json_value(row["aliases_json"]),
            )
            for row in company_rows
        )
        assignments = tuple(
            CompanyAssignment(
                publication_number=row["publication_number"],
                primary_company_id=row["company_id"],
                observed_assignee=row["observed_assignee"],
                matched_alias=row["matched_alias"],
                status=row["assignment_status"],
            )
            for row in assignment_rows
        )
        return CompanyAssignmentResult(
            companies=companies,
            assignments=assignments,
        )

    def list_fetched_documents(self, run_id: str) -> dict[str, FetchedDocument]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT publication_number,document_json,content_hash
                FROM landscape_document_fetches
                WHERE run_id = %s AND status = 'FETCHED'
                ORDER BY publication_number
                """,
                (run_id,),
            ).fetchall()
        documents: dict[str, FetchedDocument] = {}
        for row in rows:
            value = _json_value(row["document_json"])
            encoded = canonical_json(value)
            if hashlib.sha256(encoded.encode("utf-8")).hexdigest() != row["content_hash"]:
                raise ValueError(
                    f"landscape fetched document hash mismatch: {row['publication_number']}"
                )
            document = FetchedDocument.model_validate(value)
            if document.publication_number != row["publication_number"]:
                raise ValueError("fetched document publication identity mismatch")
            documents[row["publication_number"]] = document
        return documents

    def put_fetch_success(
        self,
        run_id: str,
        *,
        document_id: str,
        publication_number: str,
        document: FetchedDocument,
    ) -> None:
        publication = normalize_publication_number(publication_number)
        document_publication = normalize_publication_number(
            document.publication_number
        )
        if (
            publication is None
            or publication != publication_number
            or document_publication != publication
        ):
            raise ValueError("fetched document must match canonical publication")
        canonical_document = document.model_copy(
            update={"publication_number": publication}
        )
        value = canonical_document.model_dump(mode="json")
        assert_no_secrets(value)
        encoded = canonical_json(value)
        content_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        with self.connect() as connection:
            _require_candidate_identity(
                connection, run_id, document_id, publication
            )
            existing = connection.execute(
                """
                SELECT status,content_hash FROM landscape_document_fetches
                WHERE run_id = %s AND document_id = %s
                FOR UPDATE
                """,
                (run_id, document_id),
            ).fetchone()
            if existing is not None and existing["status"] == "FETCHED":
                if existing["content_hash"] != content_hash:
                    raise ValueError("fetched document is immutable")
                return
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO landscape_document_fetches(
                        run_id,document_id,publication_number,status,document_json,
                        content_hash,attempt_count,error_code,error_message,updated_at
                    ) VALUES(%s,%s,%s,'FETCHED',%s::jsonb,%s,1,NULL,NULL,%s)
                    """,
                    (
                        run_id,
                        document_id,
                        publication,
                        encoded,
                        content_hash,
                        now_ms(),
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE landscape_document_fetches
                    SET status='FETCHED',document_json=%s::jsonb,content_hash=%s,
                        attempt_count=attempt_count+1,error_code=NULL,
                        error_message=NULL,updated_at=%s
                    WHERE run_id=%s AND document_id=%s
                    """,
                    (encoded, content_hash, now_ms(), run_id, document_id),
                )

    def put_fetch_failure(
        self,
        run_id: str,
        *,
        document_id: str,
        publication_number: str,
        error_message: str,
    ) -> None:
        publication = normalize_publication_number(publication_number)
        if publication is None or publication != publication_number:
            raise ValueError("fetch failure publication must be canonical")
        message = error_message.strip()[:2000] or "unknown fetch failure"
        with self.connect() as connection:
            _require_candidate_identity(
                connection, run_id, document_id, publication
            )
            existing = connection.execute(
                """
                SELECT status,content_hash FROM landscape_document_fetches
                WHERE run_id = %s AND document_id = %s
                FOR UPDATE
                """,
                (run_id, document_id),
            ).fetchone()
            if existing is not None and existing["status"] == "FETCHED":
                return
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO landscape_document_fetches(
                        run_id,document_id,publication_number,status,document_json,
                        content_hash,attempt_count,error_code,error_message,updated_at
                    ) VALUES(%s,%s,%s,'FAILED',NULL,NULL,1,'FETCH_FAILED',%s,%s)
                    """,
                    (run_id, document_id, publication, message, now_ms()),
                )
            else:
                connection.execute(
                    """
                    UPDATE landscape_document_fetches
                    SET attempt_count=attempt_count+1,error_code='FETCH_FAILED',
                        error_message=%s,updated_at=%s
                    WHERE run_id=%s AND document_id=%s
                    """,
                    (message, now_ms(), run_id, document_id),
                )

    def list_patent_analyses(
        self, run_id: str
    ) -> dict[str, LandscapePatentAnalysis]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT publication_number,analysis_json,content_hash
                FROM landscape_patent_analyses
                WHERE run_id = %s
                ORDER BY publication_number
                """,
                (run_id,),
            ).fetchall()
        analyses: dict[str, LandscapePatentAnalysis] = {}
        for row in rows:
            value = _json_value(row["analysis_json"])
            encoded = canonical_json(value)
            if hashlib.sha256(encoded.encode("utf-8")).hexdigest() != row["content_hash"]:
                raise ValueError(
                    f"landscape patent analysis hash mismatch: {row['publication_number']}"
                )
            analysis = LandscapePatentAnalysis.model_validate(value)
            if analysis.publication_number != row["publication_number"]:
                raise ValueError("patent analysis publication identity mismatch")
            analyses[row["publication_number"]] = analysis
        return analyses


def _json_value(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _company_projection(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "company_id": row["company_id"],
        "canonical_name": row["canonical_name"],
        "aliases": _json_value(row.get("aliases_json", row.get("aliases", []))),
        "raw_names": _json_value(
            row.get("raw_names_json", row.get("raw_names", []))
        ),
        "resolution_source": row["resolution_source"],
        "confidence": row["confidence"],
        "content_hash": row["content_hash"],
    }


def _assignment_projection(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "publication_number": row["publication_number"],
        "company_id": row["company_id"],
        "relationship": row["relationship"],
        "observed_assignee": row["observed_assignee"],
        "matched_alias": row["matched_alias"],
        "assignment_status": row["assignment_status"],
        "confidence": row["confidence"],
        "content_hash": row["content_hash"],
    }


def _require_candidate_identity(
    connection: Any,
    run_id: str,
    document_id: str,
    publication_number: str,
) -> None:
    row = connection.execute(
        """
        SELECT publication_number FROM landscape_candidates
        WHERE run_id = %s AND document_id = %s
        """,
        (run_id, document_id),
    ).fetchone()
    if row is None or row["publication_number"] != publication_number:
        raise ValueError("fetch state must reference the canonical candidate identity")


__all__ = ["LandscapePostgreSQLDatabase"]
