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
    AssigneeScope,
    CompanyAssignment,
    CompanyTechnologyProfile,
    CrossCompanyTrendAnalysis,
    LandscapeCoverageAudit,
    LandscapeDirectionFingerprint,
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
            resolution_source = (
                f"USER_CONFIGURED_{company.assignee_scope.value}_SCOPE"
            )
            confidence = (
                0.65
                if company.assignee_scope == AssigneeScope.GROUP
                else 1.0
            )
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
            else 0.65
            if assignment.status == "CONFIRMED_GROUP_SCOPE"
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
                WHERE version = '091_landscape_v4_scale_gate'
                """
            ).fetchone()
        if row is None:
            raise PostgreSQLPersistenceError(
                "PostgreSQL schema is not current; apply migration "
                "091_landscape_v4_scale_gate"
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

    def put_direction_evidence(
        self,
        run_id: str,
        fingerprints: list[LandscapeDirectionFingerprint],
    ) -> None:
        """Persist the bounded evidence backing all-patent lightweight trends.

        Lightweight direction classification deliberately runs before optional
        deep patent analysis.  Its evidence therefore cannot depend on
        ``landscape_patent_analyses`` or the old deep-analysis evidence packet.
        The fingerprints are deterministic, run-scoped, and use canonical
        candidate document IDs so later profile/category evidence references
        remain fully auditable.
        """
        by_publication: dict[str, LandscapeDirectionFingerprint] = {}
        for fingerprint in fingerprints:
            publication = normalize_publication_number(fingerprint.publication_number)
            if publication is None or publication != fingerprint.publication_number:
                raise ValueError("direction fingerprint publication must be canonical")
            if publication in by_publication:
                raise ValueError("direction fingerprints must have unique publications")
            by_publication[publication] = fingerprint

        with self.connect() as connection:
            _lock_landscape_run(connection, run_id)
            candidate_rows = connection.execute(
                """
                SELECT document_id,publication_number
                FROM landscape_candidates
                WHERE run_id=%s
                ORDER BY publication_number
                """,
                (run_id,),
            ).fetchall()
            document_by_publication = {
                row["publication_number"]: row["document_id"]
                for row in candidate_rows
            }
            missing = sorted(set(by_publication) - set(document_by_publication))
            if missing:
                raise ValueError(
                    "direction evidence is outside canonical candidate set: "
                    + ", ".join(missing)
                )

            prepared: list[dict[str, Any]] = []
            seen_ids: set[str] = set()
            for publication in sorted(by_publication):
                fingerprint = by_publication[publication]
                for evidence in fingerprint.evidence:
                    actual_hash = hashlib.sha256(
                        evidence.text.encode("utf-8")
                    ).hexdigest()
                    if actual_hash != evidence.content_hash:
                        raise ValueError(
                            "direction evidence content hash does not match text"
                        )
                    if evidence.evidence_id in seen_ids:
                        raise ValueError("direction evidence IDs must be unique")
                    seen_ids.add(evidence.evidence_id)
                    prepared.append(
                        {
                            "evidence_id": evidence.evidence_id,
                            "document_id": document_by_publication[publication],
                            "section_type": evidence.section_type,
                            "section_label": f"LIGHTWEIGHT_{evidence.section_type}",
                            "quote_text": evidence.text,
                            "start_offset": 0,
                            "end_offset": len(evidence.text),
                            "content_hash": evidence.content_hash,
                        }
                    )

            existing_rows = connection.execute(
                """
                SELECT evidence_id,document_id,section_type,section_label,
                       quote_text,start_offset,end_offset,content_hash
                FROM landscape_evidence
                WHERE run_id=%s
                ORDER BY evidence_id
                """,
                (run_id,),
            ).fetchall()
            existing_by_id = {
                row["evidence_id"]: {
                    "evidence_id": row["evidence_id"],
                    "document_id": row["document_id"],
                    "section_type": row["section_type"],
                    "section_label": row["section_label"],
                    "quote_text": row["quote_text"],
                    "start_offset": row["start_offset"],
                    "end_offset": row["end_offset"],
                    "content_hash": row["content_hash"],
                }
                for row in existing_rows
            }
            for evidence in prepared:
                existing = existing_by_id.get(evidence["evidence_id"])
                if existing is not None:
                    if existing != evidence:
                        raise ValueError(
                            "landscape direction evidence is immutable or corrupt"
                        )
                    continue
                connection.execute(
                    """
                    INSERT INTO landscape_evidence(
                        evidence_id,run_id,document_id,section_type,section_label,
                        quote_text,start_offset,end_offset,content_hash,created_at
                    ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        evidence["evidence_id"],
                        run_id,
                        evidence["document_id"],
                        evidence["section_type"],
                        evidence["section_label"],
                        evidence["quote_text"],
                        evidence["start_offset"],
                        evidence["end_offset"],
                        evidence["content_hash"],
                        now_ms(),
                    ),
                )

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
                assignee_scope=_assignee_scope_from_resolution_source(
                    row["resolution_source"]
                ),
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

    def put_patent_analysis(
        self,
        run_id: str,
        *,
        document_id: str,
        analysis: LandscapePatentAnalysis,
    ) -> None:
        value = analysis.model_dump(mode="json")
        assert_no_secrets(value)
        encoded = canonical_json(value)
        content_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        publication = analysis.publication_number
        with self.connect() as connection:
            existing = connection.execute(
                """
                SELECT analysis_json,content_hash
                FROM landscape_patent_analyses
                WHERE run_id=%s AND document_id=%s
                """,
                (run_id, document_id),
            ).fetchone()
            if existing is not None:
                stored = canonical_json(_json_value(existing["analysis_json"]))
                if (
                    existing["content_hash"] != content_hash
                    or stored != encoded
                ):
                    raise ValueError(
                        f"landscape patent analysis is immutable: {publication}"
                    )
                return
            connection.execute(
                """
                INSERT INTO landscape_patent_analyses(
                    run_id,document_id,publication_number,analysis_json,
                    content_hash,created_at
                ) VALUES(%s,%s,%s,%s::jsonb,%s,%s)
                """,
                (run_id, document_id, publication, encoded, content_hash, now_ms()),
            )

    def put_company_profile(
        self,
        run_id: str,
        *,
        company_id: str,
        profile: CompanyTechnologyProfile,
    ) -> CompanyTechnologyProfile:
        value = profile.model_dump(mode="json")
        assert_no_secrets(value)
        encoded = canonical_json(value)
        profile_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        with self.connect() as connection:
            _lock_landscape_run(connection, run_id)
            company = connection.execute(
                """
                SELECT company_id FROM landscape_companies
                WHERE run_id = %s AND company_id = %s
                """,
                (run_id, company_id),
            ).fetchone()
            if company is None:
                raise ValueError(f"unknown landscape company: {company_id}")
            assigned_rows = connection.execute(
                """
                SELECT c.document_id,c.publication_number
                FROM landscape_candidates c
                JOIN landscape_document_companies dc
                  ON dc.run_id=c.run_id AND dc.document_id=c.document_id
                 AND dc.relationship='PRIMARY'
                WHERE c.run_id=%s AND dc.company_id=%s
                ORDER BY c.publication_number
                """,
                (run_id, company_id),
            ).fetchall()
            document_by_publication = {
                row["publication_number"]: row["document_id"]
                for row in assigned_rows
            }
            members = [
                publication
                for category in profile.technology_categories
                for publication in category.publication_numbers
            ]
            if set(members) != set(document_by_publication):
                raise ValueError(
                    "company profile must cover all assigned company candidates exactly"
                )
            evidence_rows = connection.execute(
                """
                SELECT e.evidence_id,e.document_id,c.publication_number
                FROM landscape_evidence e
                JOIN landscape_candidates c
                  ON c.run_id=e.run_id AND c.document_id=e.document_id
                WHERE e.run_id=%s
                ORDER BY e.evidence_id
                """,
                (run_id,),
            ).fetchall()
            evidence_owner = {
                row["evidence_id"]: (
                    row["document_id"],
                    row["publication_number"],
                )
                for row in evidence_rows
            }
            category_rows, member_rows, insight_rows = _prepare_profile_rows(
                company_id=company_id,
                profile=profile,
                document_by_publication=document_by_publication,
                evidence_owner=evidence_owner,
            )
            manifest = connection.execute(
                """
                SELECT category_count,member_count,content_hash
                FROM landscape_company_analysis_manifests
                WHERE run_id=%s AND company_id=%s
                """,
                (run_id, company_id),
            ).fetchone()
            stored_profile = connection.execute(
                """
                SELECT profile_json,content_hash
                FROM landscape_company_profiles
                WHERE run_id=%s AND company_id=%s
                """,
                (run_id, company_id),
            ).fetchone()
            stored_categories = connection.execute(
                """
                SELECT category_id,name,summary,keywords_json,content_hash
                FROM landscape_company_categories
                WHERE run_id=%s AND company_id=%s
                ORDER BY category_id
                """,
                (run_id, company_id),
            ).fetchall()
            stored_members = connection.execute(
                """
                SELECT category_id,document_id,publication_number
                FROM landscape_company_category_members
                WHERE run_id=%s AND company_id=%s
                ORDER BY publication_number
                """,
                (run_id, company_id),
            ).fetchall()
            stored_insights = connection.execute(
                """
                SELECT ie.insight_id,ie.evidence_id,ie.document_id
                FROM landscape_insight_evidence ie
                JOIN landscape_company_categories cc
                  ON cc.run_id=ie.run_id
                 AND ie.insight_id=cc.company_id || ':' || cc.category_id
                WHERE ie.run_id=%s AND ie.insight_type='CATEGORY'
                  AND cc.company_id=%s
                ORDER BY ie.insight_id,ie.evidence_id
                """,
                (run_id, company_id),
            ).fetchall()
            any_stored = bool(
                manifest
                or stored_profile
                or stored_categories
                or stored_members
                or stored_insights
            )
            if manifest is not None and stored_profile is not None:
                stored_encoded = canonical_json(
                    _json_value(stored_profile["profile_json"])
                )
                if (
                    manifest["category_count"] != len(category_rows)
                    or manifest["member_count"] != len(member_rows)
                    or manifest["content_hash"] != profile_hash
                    or stored_profile["content_hash"] != profile_hash
                    or stored_encoded != encoded
                    or [
                        _category_storage_projection(row)
                        for row in stored_categories
                    ]
                    != category_rows
                    or [dict(row) for row in stored_members] != member_rows
                    or [dict(row) for row in stored_insights] != insight_rows
                ):
                    raise ValueError(
                        "landscape company profile is immutable or corrupt"
                    )
                return profile
            if any_stored:
                raise ValueError("landscape company profile is partial or corrupt")

            created_at = now_ms()
            for category in category_rows:
                connection.execute(
                    """
                    INSERT INTO landscape_company_categories(
                        run_id,company_id,category_id,name,summary,keywords_json,
                        content_hash,created_at
                    ) VALUES(%s,%s,%s,%s,%s,%s::jsonb,%s,%s)
                    """,
                    (
                        run_id,
                        company_id,
                        category["category_id"],
                        category["name"],
                        category["summary"],
                        category["keywords_json"],
                        category["content_hash"],
                        created_at,
                    ),
                )
            for member in member_rows:
                connection.execute(
                    """
                    INSERT INTO landscape_company_category_members(
                        run_id,company_id,category_id,document_id,
                        publication_number,created_at
                    ) VALUES(%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        run_id,
                        company_id,
                        member["category_id"],
                        member["document_id"],
                        member["publication_number"],
                        created_at,
                    ),
                )
            for insight in insight_rows:
                connection.execute(
                    """
                    INSERT INTO landscape_insight_evidence(
                        run_id,insight_type,insight_id,evidence_id,
                        document_id,created_at
                    ) VALUES(%s,'CATEGORY',%s,%s,%s,%s)
                    """,
                    (
                        run_id,
                        insight["insight_id"],
                        insight["evidence_id"],
                        insight["document_id"],
                        created_at,
                    ),
                )
            connection.execute(
                """
                INSERT INTO landscape_company_profiles(
                    run_id,company_id,profile_json,content_hash,created_at
                ) VALUES(%s,%s,%s::jsonb,%s,%s)
                """,
                (run_id, company_id, encoded, profile_hash, created_at),
            )
            connection.execute(
                """
                INSERT INTO landscape_company_analysis_manifests(
                    run_id,company_id,category_count,member_count,
                    content_hash,created_at
                ) VALUES(%s,%s,%s,%s,%s,%s)
                """,
                (
                    run_id,
                    company_id,
                    len(category_rows),
                    len(member_rows),
                    profile_hash,
                    created_at,
                ),
            )
        return profile

    def list_company_profiles(
        self, run_id: str
    ) -> dict[str, CompanyTechnologyProfile]:
        with self.connect() as connection:
            base_rows = connection.execute(
                """
                SELECT company_id,profile_json,content_hash
                FROM landscape_company_profiles
                WHERE run_id=%s
                ORDER BY company_id
                """,
                (run_id,),
            ).fetchall()
            revision_rows = connection.execute(
                """
                SELECT DISTINCT ON (company_id) company_id,profile_json,content_hash
                FROM landscape_company_profile_revisions
                WHERE run_id=%s
                ORDER BY company_id,repair_round DESC
                """,
                (run_id,),
            ).fetchall()
        rows_by_company = {row["company_id"]: row for row in base_rows}
        rows_by_company.update({row["company_id"]: row for row in revision_rows})
        profiles = {}
        for company_id, row in sorted(rows_by_company.items()):
            value = _json_value(row["profile_json"])
            encoded = canonical_json(value)
            if hashlib.sha256(encoded.encode("utf-8")).hexdigest() != row["content_hash"]:
                raise ValueError("landscape company profile hash mismatch")
            profiles[company_id] = CompanyTechnologyProfile.model_validate(
                value
            )
        return profiles

    def put_repaired_company_profile(
        self,
        run_id: str,
        *,
        repair_round: int,
        company_id: str,
        profile: CompanyTechnologyProfile,
    ) -> CompanyTechnologyProfile:
        if repair_round < 1:
            raise ValueError("repair profile snapshots require repair_round >= 1")
        value = profile.model_dump(mode="json")
        assert_no_secrets(value)
        encoded = canonical_json(value)
        content_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        with self.connect() as connection:
            _lock_landscape_run(connection, run_id)
            company = connection.execute(
                """
                SELECT company_id FROM landscape_companies
                WHERE run_id=%s AND company_id=%s
                """,
                (run_id, company_id),
            ).fetchone()
            if company is None:
                raise ValueError(f"unknown landscape company: {company_id}")
            existing = connection.execute(
                """
                SELECT profile_json,content_hash
                FROM landscape_company_profile_revisions
                WHERE run_id=%s AND repair_round=%s AND company_id=%s
                """,
                (run_id, repair_round, company_id),
            ).fetchone()
            if existing is not None:
                if (
                    existing["content_hash"] != content_hash
                    or canonical_json(_json_value(existing["profile_json"])) != encoded
                ):
                    raise ValueError("landscape repair profile snapshot is immutable")
                return profile
            connection.execute(
                """
                INSERT INTO landscape_company_profile_revisions(
                    run_id,repair_round,company_id,profile_json,content_hash,created_at
                ) VALUES(%s,%s,%s,%s::jsonb,%s,%s)
                """,
                (run_id, repair_round, company_id, encoded, content_hash, now_ms()),
            )
        return profile

    def put_cross_company_analysis(
        self,
        run_id: str,
        analysis: CrossCompanyTrendAnalysis,
    ) -> CrossCompanyTrendAnalysis:
        value = analysis.model_dump(mode="json")
        assert_no_secrets(value)
        encoded = canonical_json(value)
        content_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        expected_trends = sorted(
            (
                _trend_storage_row(trend.model_dump(mode="json"))
                for trend in analysis.trends
            ),
            key=lambda row: row["trend_id"],
        )
        with self.connect() as connection:
            _lock_landscape_run(connection, run_id)
            snapshot = connection.execute(
                """
                SELECT analysis_json,trend_count,content_hash
                FROM landscape_cross_company_analyses
                WHERE run_id = %s
                """,
                (run_id,),
            ).fetchone()
            trend_rows = connection.execute(
                """
                SELECT trend_id,trend_json,content_hash
                FROM landscape_cross_company_trends
                WHERE run_id = %s
                ORDER BY trend_id
                """,
                (run_id,),
            ).fetchall()
            stored_trends = [
                _trend_storage_row(_json_value(row["trend_json"]))
                for row in trend_rows
            ]
            if snapshot is not None:
                stored_value = _json_value(snapshot["analysis_json"])
                if (
                    snapshot["trend_count"] != len(expected_trends)
                    or snapshot["content_hash"] != content_hash
                    or canonical_json(stored_value) != encoded
                    or stored_trends != expected_trends
                ):
                    raise ValueError(
                        "landscape cross-company analysis is immutable or corrupt"
                    )
                return analysis
            if trend_rows:
                raise ValueError(
                    "landscape cross-company analysis is partial or corrupt"
                )
            created_at = now_ms()
            for trend in expected_trends:
                connection.execute(
                    """
                    INSERT INTO landscape_cross_company_trends(
                        run_id,trend_id,trend_json,content_hash,created_at
                    ) VALUES(%s,%s,%s::jsonb,%s,%s)
                    """,
                    (
                        run_id,
                        trend["trend_id"],
                        trend["trend_json"],
                        trend["content_hash"],
                        created_at,
                    ),
                )
            connection.execute(
                """
                INSERT INTO landscape_cross_company_analyses(
                    run_id,analysis_json,trend_count,content_hash,created_at
                ) VALUES(%s,%s::jsonb,%s,%s,%s)
                """,
                (run_id, encoded, len(expected_trends), content_hash, created_at),
            )
        return analysis

    def list_cross_company_analysis(
        self, run_id: str
    ) -> CrossCompanyTrendAnalysis | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT analysis_json,trend_count,content_hash
                FROM landscape_cross_company_analysis_revisions
                WHERE run_id=%s
                ORDER BY repair_round DESC
                LIMIT 1
                """,
                (run_id,),
            ).fetchone()
            if row is None:
                row = connection.execute(
                """
                SELECT analysis_json,trend_count,content_hash
                FROM landscape_cross_company_analyses
                WHERE run_id = %s
                """,
                (run_id,),
                ).fetchone()
        if row is None:
            return None
        value = _json_value(row["analysis_json"])
        encoded = canonical_json(value)
        if hashlib.sha256(encoded.encode("utf-8")).hexdigest() != row["content_hash"]:
            raise ValueError("landscape cross-company analysis hash mismatch")
        analysis = CrossCompanyTrendAnalysis.model_validate(value)
        if len(analysis.trends) != row["trend_count"]:
            raise ValueError("landscape cross-company analysis trend count mismatch")
        return analysis

    def put_repaired_cross_company_analysis(
        self,
        run_id: str,
        *,
        repair_round: int,
        analysis: CrossCompanyTrendAnalysis,
    ) -> CrossCompanyTrendAnalysis:
        if repair_round < 1:
            raise ValueError("repair trend snapshots require repair_round >= 1")
        value = analysis.model_dump(mode="json")
        assert_no_secrets(value)
        encoded = canonical_json(value)
        content_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        with self.connect() as connection:
            _lock_landscape_run(connection, run_id)
            existing = connection.execute(
                """
                SELECT analysis_json,trend_count,content_hash
                FROM landscape_cross_company_analysis_revisions
                WHERE run_id=%s AND repair_round=%s
                """,
                (run_id, repair_round),
            ).fetchone()
            if existing is not None:
                if (
                    existing["trend_count"] != len(analysis.trends)
                    or existing["content_hash"] != content_hash
                    or canonical_json(_json_value(existing["analysis_json"])) != encoded
                ):
                    raise ValueError("landscape repair trend snapshot is immutable")
                return analysis
            connection.execute(
                """
                INSERT INTO landscape_cross_company_analysis_revisions(
                    run_id,repair_round,analysis_json,trend_count,content_hash,created_at
                ) VALUES(%s,%s,%s::jsonb,%s,%s,%s)
                """,
                (run_id, repair_round, encoded, len(analysis.trends), content_hash, now_ms()),
            )
        return analysis

    def put_coverage_audit(
        self,
        run_id: str,
        *,
        repair_round: int,
        audit: LandscapeCoverageAudit,
    ) -> LandscapeCoverageAudit:
        if repair_round < 0:
            raise ValueError("repair round cannot be negative")
        value = audit.model_dump(mode="json")
        assert_no_secrets(value)
        encoded = canonical_json(value)
        content_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        with self.connect() as connection:
            _lock_landscape_run(connection, run_id)
            existing = connection.execute(
                """
                SELECT decision,audit_json,content_hash
                FROM landscape_coverage_audits
                WHERE run_id = %s AND repair_round = %s
                """,
                (run_id, repair_round),
            ).fetchone()
            if existing is not None:
                stored = canonical_json(_json_value(existing["audit_json"]))
                if (
                    existing["decision"] != audit.decision
                    or existing["content_hash"] != content_hash
                    or stored != encoded
                ):
                    raise ValueError(
                        "landscape coverage audit round is immutable"
                    )
                return audit
            connection.execute(
                """
                INSERT INTO landscape_coverage_audits(
                    run_id,repair_round,decision,audit_json,content_hash,created_at
                ) VALUES(%s,%s,%s,%s::jsonb,%s,%s)
                """,
                (
                    run_id,
                    repair_round,
                    audit.decision,
                    encoded,
                    content_hash,
                    now_ms(),
                ),
            )
        return audit

    def list_coverage_audits(
        self, run_id: str
    ) -> list[LandscapeCoverageAudit]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT repair_round,decision,audit_json,content_hash
                FROM landscape_coverage_audits
                WHERE run_id = %s
                ORDER BY repair_round
                """,
                (run_id,),
            ).fetchall()
        audits: list[LandscapeCoverageAudit] = []
        for row in rows:
            value = _json_value(row["audit_json"])
            encoded = canonical_json(value)
            if hashlib.sha256(encoded.encode("utf-8")).hexdigest() != row["content_hash"]:
                raise ValueError("landscape coverage audit hash mismatch")
            audit = LandscapeCoverageAudit.model_validate(value)
            if audit.decision != row["decision"]:
                raise ValueError("landscape coverage audit decision mismatch")
            audits.append(audit)
        return audits


def _json_value(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _assignee_scope_from_resolution_source(value: str) -> AssigneeScope:
    """Recover the scope without changing the legacy manifest projection."""

    if value == "USER_CONFIGURED_GROUP_SCOPE":
        return AssigneeScope.GROUP
    return AssigneeScope.ENTITY


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


def _trend_storage_row(value: dict[str, Any]) -> dict[str, Any]:
    encoded = canonical_json(value)
    return {
        "trend_id": value["trend_id"],
        "trend_json": encoded,
        "content_hash": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
    }


def _category_storage_projection(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "category_id": row["category_id"],
        "name": row["name"],
        "summary": row["summary"],
        "keywords_json": canonical_json(_json_value(row["keywords_json"])),
        "content_hash": row["content_hash"],
    }


def _prepare_profile_rows(
    *,
    company_id: str,
    profile: CompanyTechnologyProfile,
    document_by_publication: dict[str, str],
    evidence_owner: dict[str, tuple[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    categories = []
    members = []
    insights = []
    for category in profile.technology_categories:
        category_value = category.model_dump(mode="json")
        encoded = canonical_json(category_value)
        categories.append(
            {
                "category_id": category.category_id,
                "name": category.name,
                "summary": category.summary,
                "keywords_json": canonical_json(category.keywords),
                "content_hash": hashlib.sha256(
                    encoded.encode("utf-8")
                ).hexdigest(),
            }
        )
        publications = set(category.publication_numbers)
        for publication in sorted(publications):
            if publication not in document_by_publication:
                raise ValueError("category publication is outside analyzed company set")
            members.append(
                {
                    "category_id": category.category_id,
                    "document_id": document_by_publication[publication],
                    "publication_number": publication,
                }
            )
        cited_publications = set()
        for evidence_id in sorted(category.evidence_ids):
            owner = evidence_owner.get(evidence_id)
            if owner is None or owner[1] not in publications:
                raise ValueError(
                    "category evidence is unknown or belongs to another patent"
                )
            cited_publications.add(owner[1])
            insights.append(
                {
                    "insight_id": f"{company_id}:{category.category_id}",
                    "evidence_id": evidence_id,
                    "document_id": owner[0],
                }
            )
        if cited_publications != publications:
            raise ValueError(
                "every category member must contribute persisted evidence"
            )
    categories.sort(key=lambda row: row["category_id"])
    members.sort(key=lambda row: row["publication_number"])
    insights.sort(key=lambda row: (row["insight_id"], row["evidence_id"]))
    return categories, members, insights


def _lock_landscape_run(connection: Any, run_id: str) -> None:
    row = connection.execute(
        "SELECT run_id FROM landscape_runs WHERE run_id = %s FOR UPDATE",
        (run_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"unknown landscape run: {run_id}")


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
