from __future__ import annotations

import unittest
import hashlib
from contextlib import contextmanager
from datetime import date

from idea.providers.base import FetchedDocument
from landscape.company_assignment import CompanyAssignmentResult
from landscape.database import canonical_json
from landscape.postgres_database import (
    LandscapePostgreSQLDatabase,
    _prepare_candidate_rows,
    _prepare_company_assignment_rows,
)
from landscape.schemas import (
    CompanyAssignment,
    CompetitorInput,
    LandscapeScope,
    LandscapeEvidenceRef,
    LandscapePatentAnalysis,
    NormalizedCompany,
)


class _Cursor:
    def __init__(self, rows=None):
        self._rows = list(rows or [])
        self.rowcount = len(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _CandidateConnection:
    def __init__(self):
        self.rows: list[dict] = []
        self.statements: list[str] = []

    def execute(self, sql, params=()):
        self.statements.append(sql)
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT document_id"):
            return _Cursor(
                sorted(
                    self.rows,
                    key=lambda row: (row["rank"], row["publication_number"]),
                )
            )
        if normalized.startswith("INSERT INTO landscape_candidates"):
            (
                run_id,
                document_id,
                publication_number,
                normalized_key,
                rank,
                decision,
                metadata_json,
                content_hash,
                created_at,
            ) = params
            self.rows.append(
                {
                    "run_id": run_id,
                    "document_id": document_id,
                    "publication_number": publication_number,
                    "normalized_key": normalized_key,
                    "rank": rank,
                    "decision": decision,
                    "metadata_json": metadata_json,
                    "content_hash": content_hash,
                    "created_at": created_at,
                }
            )
            return _Cursor()
        raise AssertionError(f"unexpected SQL: {normalized}")


class LandscapePostgreSQLCandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = LandscapePostgreSQLDatabase(
            "postgresql://test:test@localhost/test"
        )
        self.connection = _CandidateConnection()

        @contextmanager
        def connect():
            yield self.connection

        self.database.connect = connect  # type: ignore[method-assign]

    @staticmethod
    def candidates():
        return [
            {
                "document_id": "LD-2",
                "publication_number": "US 2024/001 A1",
                "normalized_key": "US2024001A1",
                "rank": 2,
                "decision": "ELIGIBLE",
                "metadata": {"title": "Second", "found_by": ["fixture"]},
            },
            {
                "document_id": "LD-1",
                "publication_number": "CN123A",
                "normalized_key": "CN123A",
                "rank": 1,
                "decision": "ELIGIBLE",
                "metadata": {"title": "First"},
            },
        ]

    def test_candidate_set_is_written_once_with_explicit_jsonb_and_read_by_rank(self) -> None:
        candidates = self.candidates()
        candidates[0]["publication_number"] = "US2024001A1"
        first = self.database.put_candidates("run-1", candidates)
        second = self.database.put_candidates("run-1", candidates)

        self.assertEqual([item["rank"] for item in first], [1, 2])
        self.assertEqual(first, second)
        self.assertEqual(len(self.connection.rows), 2)
        inserts = [
            sql
            for sql in self.connection.statements
            if "INSERT INTO landscape_candidates" in sql
        ]
        self.assertEqual(len(inserts), 2)
        self.assertTrue(all("%s::jsonb" in sql for sql in inserts))

    def test_existing_candidate_set_rejects_any_content_change(self) -> None:
        candidates = self.candidates()
        candidates[0]["publication_number"] = "US2024001A1"
        self.database.put_candidates("run-1", candidates)
        changed = self.candidates()
        changed[0]["publication_number"] = "US2024001A1"
        changed[0]["metadata"] = {"title": "Changed"}
        with self.assertRaisesRegex(ValueError, "immutable"):
            self.database.put_candidates("run-1", changed)

    def test_candidate_identity_must_be_canonical_and_ranks_contiguous(self) -> None:
        with self.assertRaisesRegex(ValueError, "canonical publication number"):
            _prepare_candidate_rows(self.candidates())
        invalid_ranks = [
            {
                "document_id": "LD-1",
                "publication_number": "CN123A",
                "normalized_key": "CN123A",
                "rank": 2,
                "metadata": {},
            }
        ]
        with self.assertRaisesRegex(ValueError, "contiguous"):
            _prepare_candidate_rows(invalid_ranks)

    def test_candidate_metadata_rejects_secrets(self) -> None:
        candidate = {
            "document_id": "LD-1",
            "publication_number": "CN123A",
            "normalized_key": "CN123A",
            "rank": 1,
            "metadata": {"provider_api_key": "must-not-persist"},
        }
        with self.assertRaisesRegex(ValueError, "sensitive field"):
            self.database.put_candidates("run-1", [candidate])
        self.assertEqual(self.connection.rows, [])


class _CompanyConnection:
    def __init__(self):
        self.candidates = [
            {"document_id": "LD-CN1A", "publication_number": "CN1A", "rank": 1}
        ]
        self.companies: list[dict] = []
        self.assignments: list[dict] = []
        self.manifest = None
        self.statements: list[str] = []

    def execute(self, sql, params=()):
        self.statements.append(sql)
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT run_id FROM landscape_runs"):
            return _Cursor([{"run_id": params[0]}])
        if normalized.startswith("SELECT document_id,publication_number"):
            return _Cursor(self.candidates)
        if normalized.startswith("SELECT company_count"):
            return _Cursor([self.manifest] if self.manifest else [])
        if normalized.startswith("SELECT company_id,canonical_name"):
            return _Cursor(self.companies)
        if normalized.startswith("SELECT c.publication_number"):
            return _Cursor(self.assignments)
        if normalized.startswith("INSERT INTO landscape_companies"):
            (
                _run_id,
                company_id,
                canonical_name,
                aliases_json,
                raw_names_json,
                resolution_source,
                confidence,
                content_hash,
                _created_at,
            ) = params
            self.companies.append(
                {
                    "company_id": company_id,
                    "canonical_name": canonical_name,
                    "aliases_json": aliases_json,
                    "raw_names_json": raw_names_json,
                    "resolution_source": resolution_source,
                    "confidence": confidence,
                    "content_hash": content_hash,
                }
            )
            return _Cursor()
        if normalized.startswith("INSERT INTO landscape_document_companies"):
            (
                _run_id,
                document_id,
                company_id,
                relationship,
                observed_assignee,
                matched_alias,
                assignment_status,
                confidence,
                content_hash,
                _created_at,
            ) = params
            publication_number = next(
                row["publication_number"]
                for row in self.candidates
                if row["document_id"] == document_id
            )
            self.assignments.append(
                {
                    "publication_number": publication_number,
                    "company_id": company_id,
                    "relationship": relationship,
                    "observed_assignee": observed_assignee,
                    "matched_alias": matched_alias,
                    "assignment_status": assignment_status,
                    "confidence": confidence,
                    "content_hash": content_hash,
                }
            )
            return _Cursor()
        if normalized.startswith(
            "INSERT INTO landscape_company_assignment_manifests"
        ):
            (
                _run_id,
                company_count,
                assignment_count,
                content_hash,
                _created_at,
            ) = params
            self.manifest = {
                "company_count": company_count,
                "assignment_count": assignment_count,
                "content_hash": content_hash,
            }
            return _Cursor()
        raise AssertionError(f"unexpected SQL: {normalized}")


class LandscapePostgreSQLCompanyAssignmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = LandscapePostgreSQLDatabase(
            "postgresql://test:test@localhost/test"
        )
        self.connection = _CompanyConnection()

        @contextmanager
        def connect():
            yield self.connection

        self.database.connect = connect  # type: ignore[method-assign]
        self.scope = LandscapeScope(
            competitors=[CompetitorInput(name="Huawei", aliases=["华为"])],
            publication_start=date(2026, 4, 1),
            publication_end=date(2026, 6, 30),
        )
        self.result = CompanyAssignmentResult(
            companies=(
                NormalizedCompany(
                    company_id="CO-HUAWEI",
                    canonical_name="Huawei",
                    aliases=["华为"],
                ),
            ),
            assignments=(
                CompanyAssignment(
                    publication_number="CN1A",
                    primary_company_id="CO-HUAWEI",
                    observed_assignee=" 华为 ",
                    matched_alias="华为",
                    status="CONFIRMED_ALIAS",
                ),
            ),
        )

    def test_company_set_is_atomic_idempotent_and_uses_explicit_jsonb(self) -> None:
        first = self.database.put_company_assignments(
            "run-1", scope=self.scope, result=self.result
        )
        second = self.database.put_company_assignments(
            "run-1", scope=self.scope, result=self.result
        )

        self.assertIs(first, self.result)
        self.assertIs(second, self.result)
        self.assertEqual(len(self.connection.companies), 1)
        self.assertEqual(len(self.connection.assignments), 1)
        self.assertEqual(self.connection.manifest["assignment_count"], 1)
        loaded = self.database.list_company_assignments("run-1")
        self.assertEqual(loaded, self.result)
        company_inserts = [
            sql
            for sql in self.connection.statements
            if "INSERT INTO landscape_companies" in sql
        ]
        self.assertEqual(len(company_inserts), 1)
        self.assertIn("%s::jsonb,%s::jsonb", company_inserts[0])

    def test_changed_or_partial_set_fails_closed(self) -> None:
        self.database.put_company_assignments(
            "run-1", scope=self.scope, result=self.result
        )
        changed = CompanyAssignmentResult(
            companies=self.result.companies,
            assignments=(
                self.result.assignments[0].model_copy(
                    update={"observed_assignee": "Huawei"}
                ),
            ),
        )
        with self.assertRaisesRegex(ValueError, "immutable"):
            self.database.put_company_assignments(
                "run-1", scope=self.scope, result=changed
            )

        self.connection.manifest = None
        with self.assertRaisesRegex(ValueError, "partial"):
            self.database.put_company_assignments(
                "run-1", scope=self.scope, result=self.result
            )

    def test_assignments_must_cover_u_and_co_assignees_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "cover canonical candidates"):
            self.database.put_company_assignments(
                "run-1",
                scope=self.scope,
                result=CompanyAssignmentResult(
                    companies=self.result.companies,
                    assignments=(),
                ),
            )
        co_assignee = self.result.assignments[0].model_copy(
            update={"co_assignees": ["Unknown Joint Owner"]}
        )
        with self.assertRaisesRegex(ValueError, "co-assignees"):
            _prepare_company_assignment_rows(
                self.scope,
                CompanyAssignmentResult(
                    companies=self.result.companies,
                    assignments=(co_assignee,),
                ),
            )

    def test_empty_set_is_frozen_by_manifest(self) -> None:
        self.connection.candidates = []
        empty = CompanyAssignmentResult(companies=(), assignments=())
        self.database.put_company_assignments(
            "run-empty", scope=self.scope, result=empty
        )
        self.assertEqual(self.connection.manifest["company_count"], 0)
        self.assertEqual(self.connection.manifest["assignment_count"], 0)
        self.assertEqual(
            self.database.list_company_assignments("run-empty"),
            empty,
        )


class _FetchConnection:
    def __init__(self):
        self.rows: dict[str, dict] = {}

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        if normalized.startswith(
            "SELECT publication_number FROM landscape_candidates"
        ):
            return _Cursor([{"publication_number": "CN1A"}])
        if normalized.startswith("SELECT publication_number,document_json"):
            return _Cursor(
                sorted(
                    (
                        row
                        for row in self.rows.values()
                        if row["status"] == "FETCHED"
                    ),
                    key=lambda row: row["publication_number"],
                )
            )
        if normalized.startswith("SELECT status,content_hash"):
            return _Cursor([self.rows[params[1]]] if params[1] in self.rows else [])
        if normalized.startswith("INSERT INTO landscape_document_fetches"):
            run_id, document_id, publication_number = params[:3]
            if "'FETCHED'" in normalized:
                document_json, content_hash, _updated_at = params[3:]
                self.rows[document_id] = {
                    "run_id": run_id,
                    "document_id": document_id,
                    "publication_number": publication_number,
                    "status": "FETCHED",
                    "document_json": document_json,
                    "content_hash": content_hash,
                    "attempt_count": 1,
                }
            else:
                message, _updated_at = params[3:]
                self.rows[document_id] = {
                    "run_id": run_id,
                    "document_id": document_id,
                    "publication_number": publication_number,
                    "status": "FAILED",
                    "document_json": None,
                    "content_hash": None,
                    "attempt_count": 1,
                    "error_message": message,
                }
            return _Cursor()
        if normalized.startswith("UPDATE landscape_document_fetches"):
            document_id = params[-1]
            row = self.rows[document_id]
            if "SET status='FETCHED'" in normalized:
                document_json, content_hash, _updated_at, _run_id, _document_id = params
                row.update(
                    {
                        "status": "FETCHED",
                        "document_json": document_json,
                        "content_hash": content_hash,
                        "attempt_count": row["attempt_count"] + 1,
                    }
                )
            else:
                message, _updated_at, _run_id, _document_id = params
                row["attempt_count"] += 1
                row["error_message"] = message
            return _Cursor()
        raise AssertionError(f"unexpected SQL: {normalized}")


class LandscapePostgreSQLFetchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = LandscapePostgreSQLDatabase(
            "postgresql://test:test@localhost/test"
        )
        self.connection = _FetchConnection()

        @contextmanager
        def connect():
            yield self.connection

        self.database.connect = connect  # type: ignore[method-assign]
        self.document = FetchedDocument(
            provider="fixture",
            publication_number="CN1A",
            title="冷却专利",
            url="https://example.test/CN1A",
            abstract_text="摘要",
            claims_text="权利要求",
            description_text="说明书",
        )

    def test_failure_is_retryable_and_success_is_resumable_and_immutable(self) -> None:
        self.database.put_fetch_failure(
            "run-1",
            document_id="LD-CN1A",
            publication_number="CN1A",
            error_message="timeout",
        )
        self.database.put_fetch_failure(
            "run-1",
            document_id="LD-CN1A",
            publication_number="CN1A",
            error_message="timeout again",
        )
        self.assertEqual(self.connection.rows["LD-CN1A"]["attempt_count"], 2)

        self.database.put_fetch_success(
            "run-1",
            document_id="LD-CN1A",
            publication_number="CN1A",
            document=self.document,
        )
        self.assertEqual(
            self.database.list_fetched_documents("run-1"),
            {"CN1A": self.document},
        )
        self.database.put_fetch_failure(
            "run-1",
            document_id="LD-CN1A",
            publication_number="CN1A",
            error_message="late failure",
        )
        self.assertEqual(self.connection.rows["LD-CN1A"]["status"], "FETCHED")

        changed = self.document.model_copy(update={"title": "changed"})
        with self.assertRaisesRegex(ValueError, "immutable"):
            self.database.put_fetch_success(
                "run-1",
                document_id="LD-CN1A",
                publication_number="CN1A",
                document=changed,
            )


class LandscapePostgreSQLAnalysisReadTests(unittest.TestCase):
    def test_analysis_read_validates_hash_and_publication_identity(self) -> None:
        database = LandscapePostgreSQLDatabase(
            "postgresql://test:test@localhost/test"
        )
        analysis = LandscapePatentAnalysis(
            publication_number="CN1A",
            prior_art="现有方案",
            core_invention_points=["核心改进"],
            evidence_refs=[
                LandscapeEvidenceRef(
                    evidence_id="EV-CN1A",
                    supports=["prior_art", "core_invention_point"],
                )
            ],
        )
        encoded = canonical_json(analysis.model_dump(mode="json"))
        row = {
            "publication_number": "CN1A",
            "analysis_json": encoded,
            "content_hash": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        }

        @contextmanager
        def connect():
            class Connection:
                def execute(self, _sql, _params=()):
                    return _Cursor([row])

            yield Connection()

        database.connect = connect  # type: ignore[method-assign]
        self.assertEqual(
            database.list_patent_analyses("run-1"),
            {"CN1A": analysis},
        )
        row["content_hash"] = "corrupt"
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            database.list_patent_analyses("run-1")


if __name__ == "__main__":
    unittest.main()
