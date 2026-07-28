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
    _prepare_profile_rows,
)
from landscape.schemas import (
    CompanyAssignment,
    CompetitorInput,
    CrossCompanyTrendAnalysis,
    LandscapeCoverageAudit,
    LandscapeScope,
    LandscapeEvidenceRef,
    LandscapePatentAnalysis,
    NormalizedCompany,
)
from tests.test_landscape_company_trends import profile


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


class _ResultConnection:
    def __init__(self):
        self.snapshot = None
        self.trends = []
        self.audits = {}

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT run_id FROM landscape_runs"):
            return _Cursor([{"run_id": params[0]}])
        if normalized.startswith("SELECT analysis_json,trend_count"):
            return _Cursor([self.snapshot] if self.snapshot else [])
        if normalized.startswith("SELECT trend_id,trend_json"):
            return _Cursor(sorted(self.trends, key=lambda row: row["trend_id"]))
        if normalized.startswith("INSERT INTO landscape_cross_company_trends"):
            _run_id, trend_id, trend_json, content_hash, _created_at = params
            self.trends.append(
                {
                    "trend_id": trend_id,
                    "trend_json": trend_json,
                    "content_hash": content_hash,
                }
            )
            return _Cursor()
        if normalized.startswith("INSERT INTO landscape_cross_company_analyses"):
            _run_id, analysis_json, trend_count, content_hash, _created_at = params
            self.snapshot = {
                "analysis_json": analysis_json,
                "trend_count": trend_count,
                "content_hash": content_hash,
            }
            return _Cursor()
        if normalized.startswith("SELECT decision,audit_json"):
            row = self.audits.get(params[1])
            return _Cursor([row] if row else [])
        if normalized.startswith("INSERT INTO landscape_coverage_audits"):
            (
                _run_id,
                repair_round,
                decision,
                audit_json,
                content_hash,
                _created_at,
            ) = params
            self.audits[repair_round] = {
                "repair_round": repair_round,
                "decision": decision,
                "audit_json": audit_json,
                "content_hash": content_hash,
            }
            return _Cursor()
        if normalized.startswith("SELECT repair_round,decision"):
            return _Cursor(
                [self.audits[key] for key in sorted(self.audits)]
            )
        raise AssertionError(f"unexpected SQL: {normalized}")


class LandscapePostgreSQLResultSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = LandscapePostgreSQLDatabase(
            "postgresql://test:test@localhost/test"
        )
        self.connection = _ResultConnection()

        @contextmanager
        def connect():
            yield self.connection

        self.database.connect = connect  # type: ignore[method-assign]

    def test_zero_trend_analysis_is_a_durable_idempotent_result(self) -> None:
        analysis = CrossCompanyTrendAnalysis(
            overall_summary="仅一家公司，无法跨公司比较。",
            limitations=["至少需要两家公司。"],
        )
        self.database.put_cross_company_analysis("run-1", analysis)
        self.database.put_cross_company_analysis("run-1", analysis)

        self.assertEqual(self.connection.snapshot["trend_count"], 0)
        self.assertEqual(self.connection.trends, [])
        self.assertEqual(
            self.database.list_cross_company_analysis("run-1"),
            analysis,
        )
        changed = analysis.model_copy(
            update={"overall_summary": "changed"}
        )
        with self.assertRaisesRegex(ValueError, "immutable"):
            self.database.put_cross_company_analysis("run-1", changed)

    def test_audit_rounds_are_append_only_and_read_in_order(self) -> None:
        first = LandscapeCoverageAudit(
            decision="REPAIR",
            coverage_ratio=0.5,
            missing_publications=["CN2A"],
            repair_targets=["ANALYZE:CN2A"],
        )
        second = LandscapeCoverageAudit(
            decision="PASS",
            coverage_ratio=1,
        )
        self.database.put_coverage_audit(
            "run-1", repair_round=0, audit=first
        )
        self.database.put_coverage_audit(
            "run-1", repair_round=1, audit=second
        )
        self.database.put_coverage_audit(
            "run-1", repair_round=0, audit=first
        )
        self.assertEqual(
            self.database.list_coverage_audits("run-1"),
            [first, second],
        )
        with self.assertRaisesRegex(ValueError, "immutable"):
            self.database.put_coverage_audit(
                "run-1", repair_round=0, audit=second
            )


class LandscapePostgreSQLCompanyProfilePreparationTests(unittest.TestCase):
    def test_profile_rows_preserve_members_and_evidence_ownership(self) -> None:
        company_profile = profile("A", ["CN1A", "CN2A"])
        categories, members, insights = _prepare_profile_rows(
            company_id="CO-A",
            profile=company_profile,
            document_by_publication={
                "CN1A": "LD-1",
                "CN2A": "LD-2",
            },
            evidence_owner={
                "EV-CN1A": ("LD-1", "CN1A"),
                "EV-CN2A": ("LD-2", "CN2A"),
            },
        )
        self.assertEqual(len(categories), 1)
        self.assertEqual(
            [member["publication_number"] for member in members],
            ["CN1A", "CN2A"],
        )
        self.assertEqual(
            [insight["evidence_id"] for insight in insights],
            ["EV-CN1A", "EV-CN2A"],
        )
        self.assertTrue(categories[0]["content_hash"])

    def test_cross_patent_or_incomplete_evidence_fails_closed(self) -> None:
        company_profile = profile("A", ["CN1A", "CN2A"])
        with self.assertRaisesRegex(ValueError, "another patent"):
            _prepare_profile_rows(
                company_id="CO-A",
                profile=company_profile,
                document_by_publication={
                    "CN1A": "LD-1",
                    "CN2A": "LD-2",
                },
                evidence_owner={
                    "EV-CN1A": ("LD-X", "EP9A1"),
                    "EV-CN2A": ("LD-2", "CN2A"),
                },
            )
        incomplete = company_profile.model_copy(
            update={
                "technology_categories": [
                    company_profile.technology_categories[0].model_copy(
                        update={"evidence_ids": ["EV-CN1A"]}
                    )
                ]
            }
        )
        with self.assertRaisesRegex(ValueError, "must contribute"):
            _prepare_profile_rows(
                company_id="CO-A",
                profile=incomplete,
                document_by_publication={
                    "CN1A": "LD-1",
                    "CN2A": "LD-2",
                },
                evidence_owner={
                    "EV-CN1A": ("LD-1", "CN1A"),
                    "EV-CN2A": ("LD-2", "CN2A"),
                },
            )


class _CompanyProfileConnection:
    def __init__(self):
        self.categories = []
        self.members = []
        self.insights = []
        self.profile = None
        self.manifest = None

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT run_id FROM landscape_runs"):
            return _Cursor([{"run_id": params[0]}])
        if normalized.startswith("SELECT company_id FROM landscape_companies"):
            return _Cursor([{"company_id": "CO_A"}])
        if normalized.startswith("SELECT c.document_id,c.publication_number"):
            return _Cursor(
                [
                    {"document_id": "LD-1", "publication_number": "CN1A"},
                    {"document_id": "LD-2", "publication_number": "CN2A"},
                ]
            )
        if normalized.startswith("SELECT e.evidence_id"):
            return _Cursor(
                [
                    {
                        "evidence_id": "EV-CN1A",
                        "document_id": "LD-1",
                        "publication_number": "CN1A",
                    },
                    {
                        "evidence_id": "EV-CN2A",
                        "document_id": "LD-2",
                        "publication_number": "CN2A",
                    },
                ]
            )
        if normalized.startswith("SELECT category_count"):
            return _Cursor([self.manifest] if self.manifest else [])
        if normalized.startswith("SELECT profile_json"):
            return _Cursor([self.profile] if self.profile else [])
        if normalized.startswith("SELECT category_id,name"):
            return _Cursor(self.categories)
        if normalized.startswith("SELECT category_id,document_id"):
            return _Cursor(self.members)
        if normalized.startswith("SELECT ie.insight_id"):
            if " LIKE " in normalized:
                raise AssertionError("company insight lookup must not use LIKE")
            return _Cursor(self.insights)
        if normalized.startswith("INSERT INTO landscape_company_categories"):
            (
                _run_id,
                _company_id,
                category_id,
                name,
                summary,
                keywords_json,
                content_hash,
                _created_at,
            ) = params
            self.categories.append(
                {
                    "category_id": category_id,
                    "name": name,
                    "summary": summary,
                    "keywords_json": keywords_json,
                    "content_hash": content_hash,
                }
            )
            return _Cursor()
        if normalized.startswith(
            "INSERT INTO landscape_company_category_members"
        ):
            (
                _run_id,
                _company_id,
                category_id,
                document_id,
                publication_number,
                _created_at,
            ) = params
            self.members.append(
                {
                    "category_id": category_id,
                    "document_id": document_id,
                    "publication_number": publication_number,
                }
            )
            return _Cursor()
        if normalized.startswith("INSERT INTO landscape_insight_evidence"):
            (
                _run_id,
                insight_id,
                evidence_id,
                document_id,
                _created_at,
            ) = params
            self.insights.append(
                {
                    "insight_id": insight_id,
                    "evidence_id": evidence_id,
                    "document_id": document_id,
                }
            )
            return _Cursor()
        if normalized.startswith("INSERT INTO landscape_company_profiles"):
            _run_id, _company_id, profile_json, content_hash, _created_at = params
            self.profile = {
                "profile_json": profile_json,
                "content_hash": content_hash,
            }
            return _Cursor()
        if normalized.startswith(
            "INSERT INTO landscape_company_analysis_manifests"
        ):
            (
                _run_id,
                _company_id,
                category_count,
                member_count,
                content_hash,
                _created_at,
            ) = params
            self.manifest = {
                "category_count": category_count,
                "member_count": member_count,
                "content_hash": content_hash,
            }
            return _Cursor()
        raise AssertionError(f"unexpected SQL: {normalized}")


class LandscapePostgreSQLCompanyProfilePersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = LandscapePostgreSQLDatabase(
            "postgresql://test:test@localhost/test"
        )
        self.connection = _CompanyProfileConnection()

        @contextmanager
        def connect():
            yield self.connection

        self.database.connect = connect  # type: ignore[method-assign]

    def test_profile_write_is_complete_and_idempotent_for_company_with_underscore(
        self,
    ) -> None:
        company_profile = profile("A", ["CN1A", "CN2A"])

        first = self.database.put_company_profile(
            "run-1", company_id="CO_A", profile=company_profile
        )
        second = self.database.put_company_profile(
            "run-1", company_id="CO_A", profile=company_profile
        )

        self.assertEqual(first, second)
        self.assertEqual(len(self.connection.categories), 1)
        self.assertEqual(len(self.connection.members), 2)
        self.assertEqual(len(self.connection.insights), 2)
        self.assertEqual(self.connection.manifest["member_count"], 2)

    def test_profile_replay_detects_granular_category_mutation(self) -> None:
        company_profile = profile("A", ["CN1A", "CN2A"])
        self.database.put_company_profile(
            "run-1", company_id="CO_A", profile=company_profile
        )
        self.connection.categories[0]["summary"] = "bypassed mutation"

        with self.assertRaisesRegex(ValueError, "immutable or corrupt"):
            self.database.put_company_profile(
                "run-1", company_id="CO_A", profile=company_profile
            )


if __name__ == "__main__":
    unittest.main()
