from __future__ import annotations

import copy
import json
import os
import unittest
import uuid
from contextlib import contextmanager
from datetime import date

from landscape.scope import (
    CandidateSource,
    CandidateStatus,
    CompanyNameRelation,
    CompanyScopeDraft,
    NameLanguage,
    ScopeDraft,
    ScopeDraftLimitation,
    ScopeDraftStatus,
    TechnologyTermRelation,
    freeze_scope_draft,
    make_company_name_candidate,
    make_company_profile_id,
    make_scope_draft_id,
    make_technology_term_candidate,
)
from landscape.scope_repository import (
    PostgreSQLScopeDraftRepository,
    ScopePersistenceError,
    ScopeRevisionConflict,
    prepare_company_profile_rows,
    prepare_confirmed_scope_rows,
    prepare_scope_draft_rows,
    validate_persisted_scope_draft,
    validate_persisted_confirmed_scope,
    validate_persisted_company_memory,
)


class _Result:
    def __init__(self, *, row=None, rows=None, rowcount=0):
        self._row = row
        self._rows = list(rows or [])
        self.rowcount = rowcount

    def fetchone(self):
        return self._row

    def fetchall(self):
        return list(self._rows)


class _BatchCursor:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def executemany(self, sql, values):
        self.connection.batches.append((" ".join(sql.split()), list(values)))


class _ScopeConnection:
    def __init__(self, rows, *, inserted=True, current_revision=None, update_count=1):
        self.rows = rows
        self.inserted = inserted
        self.current_revision = current_revision
        self.update_count = update_count
        self.statements = []
        self.batches = []

    def cursor(self):
        return _BatchCursor(self)

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        self.statements.append((normalized, params))
        if normalized.startswith("INSERT INTO landscape_v4_scope_drafts"):
            return _Result(
                row={"draft_id": self.rows.draft["draft_id"]}
                if self.inserted
                else None,
                rowcount=int(self.inserted),
            )
        if normalized.startswith("INSERT INTO landscape_v4_scope_draft_revisions"):
            return _Result(rowcount=1)
        if normalized.startswith("SELECT draft_id,revision,status FROM"):
            if self.current_revision is None:
                return _Result()
            return _Result(
                row={
                    "draft_id": self.rows.draft["draft_id"],
                    "revision": self.current_revision,
                    "status": "DRAFT",
                }
            )
        if normalized.startswith("UPDATE landscape_v4_scope_drafts"):
            return _Result(rowcount=self.update_count)
        if "FROM landscape_v4_scope_drafts" in normalized:
            return _Result(row=self.rows.draft)
        if "FROM landscape_v4_scope_draft_revisions" in normalized:
            return _Result(row=self.rows.revision)
        if "FROM landscape_v4_scope_draft_companies" in normalized:
            return _Result(rows=self.rows.companies)
        if "FROM landscape_v4_scope_draft_names" in normalized:
            return _Result(rows=self.rows.names)
        if "FROM landscape_v4_scope_draft_terms" in normalized:
            return _Result(rows=self.rows.terms)
        if "FROM landscape_v4_scope_draft_limitations" in normalized:
            return _Result(rows=self.rows.limitations)
        raise AssertionError(f"unexpected SQL: {normalized}")


def repository_with(connection):
    @contextmanager
    def connect():
        yield connection

    return PostgreSQLScopeDraftRepository("postgresql://test", connect=connect)


def fixture() -> ScopeDraft:
    profile_id = make_company_profile_id("华为")
    names = (
        make_company_name_candidate(
            profile_id=profile_id,
            text="华为技术有限公司",
            language=NameLanguage.ZH,
            relation_type=CompanyNameRelation.LEGAL_NAME,
            source=CandidateSource.USER_INPUT,
            status=CandidateStatus.ACTIVE,
        ),
        make_company_name_candidate(
            profile_id=profile_id,
            text="Huawei Technologies Co., Ltd.",
            language=NameLanguage.EN,
            relation_type=CompanyNameRelation.TRANSLATION,
            source=CandidateSource.MODEL_SUGGESTED,
            status=CandidateStatus.PROPOSED,
        ),
    )
    terms = (
        make_technology_term_candidate(
            text="无线通信",
            language=NameLanguage.ZH,
            relation_to_original=TechnologyTermRelation.ORIGINAL,
            source=CandidateSource.USER_INPUT,
            status=CandidateStatus.ACTIVE,
        ),
        make_technology_term_candidate(
            text="wireless communication",
            language=NameLanguage.EN,
            relation_to_original=TechnologyTermRelation.TRANSLATION,
            source=CandidateSource.MODEL_SUGGESTED,
            status=CandidateStatus.PROPOSED,
        ),
    )
    return ScopeDraft(
        draft_id=make_scope_draft_id("repository-fixture"),
        revision=2,
        status=ScopeDraftStatus.AWAITING_CONFIRMATION,
        publication_start=date(2001, 1, 1),
        publication_end=date(2025, 12, 31),
        companies=(
            CompanyScopeDraft(
                profile_id=profile_id,
                display_name="华为",
                input_name="华为",
                names=names,
            ),
        ),
        technology_input="无线通信",
        technology_terms=terms,
    )


def reviewed_fixture() -> ScopeDraft:
    payload = fixture().model_dump(mode="json")
    for company_value in payload["companies"]:
        for name in company_value["names"]:
            if name["status"] == "PROPOSED":
                name["status"] = "EXCLUDED"
    for term in payload["technology_terms"]:
        if term["status"] == "PROPOSED":
            term["status"] = "ACTIVE"
    return ScopeDraft.model_validate(payload)


class LandscapeScopeRowCodecTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scope = fixture()
        self.rows = prepare_scope_draft_rows(self.scope, created_at=1234)

    def test_rows_are_complete_deterministic_and_keep_full_date_range(self) -> None:
        duplicate = prepare_scope_draft_rows(self.scope, created_at=1234)
        self.assertEqual(duplicate, self.rows)
        self.assertEqual(self.rows.draft["publication_start"], "2001-01-01")
        self.assertEqual(self.rows.draft["publication_end"], "2025-12-31")
        self.assertEqual(len(self.rows.draft["content_hash"]), 64)
        self.assertEqual(len(self.rows.companies), 1)
        self.assertEqual(len(self.rows.names), 2)
        self.assertEqual(len(self.rows.terms), 2)

    def test_relational_rows_round_trip_to_validated_scope(self) -> None:
        stored = validate_persisted_scope_draft(
            self.rows.draft,
            self.rows.revision,
            self.rows.companies,
            self.rows.names,
            self.rows.terms,
        )
        self.assertEqual(stored, self.scope)

    def test_json_string_from_database_driver_is_supported(self) -> None:
        revision = copy.deepcopy(self.rows.revision)
        revision["content_json"] = json.dumps(
            revision["content_json"], ensure_ascii=False
        )
        self.assertEqual(
            validate_persisted_scope_draft(
                self.rows.draft,
                revision,
                self.rows.companies,
                self.rows.names,
                self.rows.terms,
            ),
            self.scope,
        )

    def test_header_or_revision_tampering_fails_closed(self) -> None:
        mutations = []
        draft_row = copy.deepcopy(self.rows.draft)
        draft_row["mode"] = "COMPANY_ONLY"
        mutations.append((draft_row, self.rows.revision))
        revision_row = copy.deepcopy(self.rows.revision)
        revision_row["content_hash"] = "0" * 64
        mutations.append((self.rows.draft, revision_row))
        for draft_value, revision_value in mutations:
            with self.subTest(draft=draft_value, revision=revision_value):
                with self.assertRaises(ScopePersistenceError):
                    validate_persisted_scope_draft(
                        draft_value,
                        revision_value,
                        self.rows.companies,
                        self.rows.names,
                        self.rows.terms,
                    )

    def test_missing_reordered_or_modified_relational_row_fails_closed(self) -> None:
        cases = []
        cases.append((self.rows.names[:-1], self.rows.terms))
        cases.append((tuple(reversed(self.rows.names)), self.rows.terms))
        changed_terms = list(copy.deepcopy(self.rows.terms))
        changed_terms[0]["term_text"] = "篡改"
        cases.append((self.rows.names, tuple(changed_terms)))
        for names, terms in cases:
            with self.subTest(names=names, terms=terms):
                with self.assertRaises(ScopePersistenceError):
                    validate_persisted_scope_draft(
                        self.rows.draft,
                        self.rows.revision,
                        self.rows.companies,
                        names,
                        terms,
                    )

    def test_invalid_timestamp_is_rejected(self) -> None:
        for value in (-1, True, 1.5):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    prepare_scope_draft_rows(self.scope, created_at=value)  # type: ignore[arg-type]

    def test_user_visible_limitations_round_trip_with_draft_revision(self) -> None:
        limited = self.scope.model_copy(
            update={
                "limitations": (
                    ScopeDraftLimitation(
                        code="COMPANY_EXPANSION_FAILED",
                        object_key="华为",
                        message="公司名称扩展失败，已保留历史名称和用户输入。",
                    ),
                )
            }
        )
        rows = prepare_scope_draft_rows(limited, created_at=1234)
        self.assertEqual(rows.limitations[0]["sort_order"], 1)
        self.assertEqual(
            validate_persisted_scope_draft(
                rows.draft,
                rows.revision,
                rows.companies,
                rows.names,
                rows.terms,
                rows.limitations,
            ),
            limited,
        )


class LandscapeConfirmedScopeRowCodecTests(unittest.TestCase):
    def setUp(self) -> None:
        self.draft = reviewed_fixture()
        self.profile_id = self.draft.companies[0].profile_id
        self.confirmed = freeze_scope_draft(self.draft, {self.profile_id: 3})
        self.scope_rows = prepare_confirmed_scope_rows(self.confirmed, created_at=1234)
        self.profile_rows = prepare_company_profile_rows(
            self.draft.companies[0],
            profile_version=3,
            confirmed_scope_revision_id=self.confirmed.scope_revision_id,
            created_at=1234,
        )

    def test_company_memory_keeps_active_and_excluded_review_decisions(self) -> None:
        self.assertEqual(self.profile_rows.version["version"], 3)
        self.assertEqual(
            [row["status"] for row in self.profile_rows.names],
            ["ACTIVE", "EXCLUDED"],
        )
        self.assertEqual(len(self.profile_rows.version["snapshot_hash"]), 64)
        self.assertEqual(
            self.profile_rows.version["confirmed_scope_revision_id"],
            self.confirmed.scope_revision_id,
        )

    def test_unreviewed_company_name_cannot_enter_long_term_memory(self) -> None:
        unreviewed = fixture().companies[0]
        with self.assertRaisesRegex(ScopePersistenceError, "unresolved"):
            prepare_company_profile_rows(
                unreviewed,
                profile_version=1,
                confirmed_scope_revision_id=self.confirmed.scope_revision_id,
            )

    def test_confirmed_scope_contains_only_active_names_and_profile_version(self) -> None:
        self.assertEqual(self.scope_rows.companies[0]["profile_version"], 3)
        self.assertEqual(len(self.scope_rows.names), 1)
        self.assertEqual(self.scope_rows.names[0]["name_text"], "华为技术有限公司")
        self.assertEqual(self.scope_rows.revision["publication_start"], "2001-01-01")
        self.assertEqual(self.scope_rows.revision["publication_end"], "2025-12-31")

    def test_confirmed_relational_rows_round_trip_and_tampering_fails(self) -> None:
        self.assertEqual(
            validate_persisted_confirmed_scope(
                self.scope_rows.revision,
                self.scope_rows.companies,
                self.scope_rows.names,
                self.scope_rows.terms,
            ),
            self.confirmed,
        )
        changed = list(copy.deepcopy(self.scope_rows.companies))
        changed[0]["profile_version"] = 4
        with self.assertRaises(ScopePersistenceError):
            validate_persisted_confirmed_scope(
                self.scope_rows.revision,
                changed,
                self.scope_rows.names,
                self.scope_rows.terms,
            )

    def test_confirmed_snapshot_hash_is_revalidated(self) -> None:
        changed = copy.deepcopy(self.scope_rows.revision)
        changed["snapshot_json"]["publication_end"] = "2026-12-31"
        with self.assertRaisesRegex(ScopePersistenceError, "hash"):
            validate_persisted_confirmed_scope(
                changed,
                self.scope_rows.companies,
                self.scope_rows.names,
                self.scope_rows.terms,
            )

    def test_company_memory_round_trip_marks_history_and_preserves_rejection(self) -> None:
        profile = {
            **self.profile_rows.profile,
            "snapshot_hash": self.profile_rows.version["snapshot_hash"],
        }
        memory = validate_persisted_company_memory(
            profile,
            self.profile_rows.names,
            requested_input_name="Huawei Technologies Co., Ltd.",
        )
        self.assertEqual(memory.profile_version, 3)
        self.assertEqual(memory.company.input_name, "Huawei Technologies Co., Ltd.")
        self.assertTrue(
            all(item.source == CandidateSource.HISTORY for item in memory.company.names)
        )
        self.assertEqual(
            [item.status for item in memory.company.names],
            [CandidateStatus.ACTIVE, CandidateStatus.EXCLUDED],
        )

    def test_company_memory_order_or_hash_tampering_fails_closed(self) -> None:
        profile = {
            **self.profile_rows.profile,
            "snapshot_hash": self.profile_rows.version["snapshot_hash"],
        }
        reversed_names = tuple(reversed(self.profile_rows.names))
        with self.assertRaisesRegex(ScopePersistenceError, "order"):
            validate_persisted_company_memory(
                profile, reversed_names, requested_input_name="华为"
            )
        changed = dict(profile)
        changed["snapshot_hash"] = "0" * 64
        with self.assertRaisesRegex(ScopePersistenceError, "hash"):
            validate_persisted_company_memory(
                changed, self.profile_rows.names, requested_input_name="华为"
            )


class LandscapeScopeDraftRepositoryTests(unittest.TestCase):
    def test_create_writes_one_snapshot_and_reads_it_back(self) -> None:
        scope = fixture().model_copy(update={"revision": 1, "status": ScopeDraftStatus.DRAFT})
        rows = prepare_scope_draft_rows(scope, created_at=1234)
        connection = _ScopeConnection(rows)

        stored = repository_with(connection).create(scope)

        self.assertEqual(stored, scope)
        self.assertEqual(len(connection.batches), 3)
        self.assertEqual([len(batch[1]) for batch in connection.batches], [1, 2, 2])

    def test_create_is_idempotent_but_same_id_different_content_conflicts(self) -> None:
        scope = fixture().model_copy(update={"revision": 1, "status": ScopeDraftStatus.DRAFT})
        rows = prepare_scope_draft_rows(scope, created_at=1234)
        self.assertEqual(repository_with(_ScopeConnection(rows, inserted=False)).create(scope), scope)

        changed = scope.model_copy(update={"publication_end": date(2024, 12, 31)})
        with self.assertRaisesRegex(ScopeRevisionConflict, "different content"):
            repository_with(_ScopeConnection(rows, inserted=False)).create(changed)

    def test_new_draft_must_start_at_revision_one(self) -> None:
        connection = _ScopeConnection(prepare_scope_draft_rows(fixture(), created_at=1))
        with self.assertRaisesRegex(ScopePersistenceError, "revision 1"):
            repository_with(connection).create(fixture())
        self.assertEqual(connection.statements, [])

    def test_update_uses_locked_compare_and_swap_and_appends_revision(self) -> None:
        scope = fixture().model_copy(update={"revision": 2, "status": ScopeDraftStatus.AWAITING_CONFIRMATION})
        rows = prepare_scope_draft_rows(scope, created_at=1234)
        connection = _ScopeConnection(rows, current_revision=1)

        stored = repository_with(connection).update(scope, expected_revision=1)

        self.assertEqual(stored, scope)
        self.assertTrue(any("FOR UPDATE" in sql for sql, _ in connection.statements))
        self.assertTrue(any(sql.startswith("UPDATE landscape_v4_scope_drafts") for sql, _ in connection.statements))
        self.assertEqual(len(connection.batches), 3)

    def test_update_rejects_stale_revision_before_writing(self) -> None:
        scope = fixture().model_copy(update={"revision": 2})
        rows = prepare_scope_draft_rows(scope, created_at=1234)
        connection = _ScopeConnection(rows, current_revision=2)
        with self.assertRaisesRegex(ScopeRevisionConflict, "found 2"):
            repository_with(connection).update(scope, expected_revision=1)
        self.assertFalse(any(sql.startswith("UPDATE ") for sql, _ in connection.statements))

    def test_update_rejects_revision_gaps_and_confirmed_status(self) -> None:
        revision_gap = fixture().model_copy(update={"revision": 4})
        rows = prepare_scope_draft_rows(revision_gap, created_at=1234)
        with self.assertRaisesRegex(ScopeRevisionConflict, "exactly"):
            repository_with(_ScopeConnection(rows)).update(revision_gap, expected_revision=1)

        confirmed = fixture().model_copy(update={"revision": 2, "status": ScopeDraftStatus.CONFIRMED})
        confirmed_rows = prepare_scope_draft_rows(confirmed, created_at=1234)
        with self.assertRaisesRegex(ScopeRevisionConflict, "confirmed"):
            repository_with(_ScopeConnection(confirmed_rows, current_revision=1)).update(
                confirmed, expected_revision=1
            )
@unittest.skipUnless(
    os.environ.get("AIFPATENT_RUN_SCOPE_INTEGRATION") == "1",
    "set AIFPATENT_RUN_SCOPE_INTEGRATION=1 to test real PostgreSQL Scope storage",
)
class LandscapeScopeDraftPostgreSQLIntegrationTests(unittest.TestCase):
    def test_create_read_update_and_conflict_are_atomic_and_rollback_cleanly(self) -> None:
        import psycopg
        from psycopg.rows import dict_row

        raw = psycopg.connect(
            os.environ["AIFPATENT_POSTGRES_DSN"],
            row_factory=dict_row,
            connect_timeout=10,
        )
        draft_id = make_scope_draft_id(f"integration-{uuid.uuid4().hex}")
        unique_name = f"集成测试公司-{uuid.uuid4().hex}"
        profile_id = make_company_profile_id(unique_name)
        company_value = CompanyScopeDraft(
            profile_id=profile_id,
            display_name=unique_name,
            input_name=unique_name,
            names=(
                make_company_name_candidate(
                    profile_id=profile_id,
                    text=unique_name,
                    language=NameLanguage.ZH,
                    relation_type=CompanyNameRelation.LEGAL_NAME,
                    source=CandidateSource.USER_INPUT,
                    status=CandidateStatus.ACTIVE,
                ),
                make_company_name_candidate(
                    profile_id=profile_id,
                    text=f"Integration Company {uuid.uuid4().hex}",
                    language=NameLanguage.EN,
                    relation_type=CompanyNameRelation.TRANSLATION,
                    source=CandidateSource.MODEL_SUGGESTED,
                    status=CandidateStatus.EXCLUDED,
                ),
            ),
        )
        base = reviewed_fixture()
        initial = ScopeDraft(
            draft_id=draft_id,
            revision=1,
            status=ScopeDraftStatus.DRAFT,
            publication_start=base.publication_start,
            publication_end=base.publication_end,
            companies=(company_value,),
            technology_input=base.technology_input,
            technology_terms=base.technology_terms,
        )

        @contextmanager
        def connect():
            yield raw

        repository = PostgreSQLScopeDraftRepository(
            "postgresql://integration", connect=connect
        )
        try:
            self.assertEqual(repository.create(initial), initial)
            self.assertEqual(repository.create(initial), initial)
            self.assertEqual(repository.get(draft_id), initial)

            updated = initial.model_copy(
                update={
                    "revision": 2,
                    "status": ScopeDraftStatus.AWAITING_CONFIRMATION,
                    "publication_end": date(2026, 12, 31),
                }
            )
            self.assertEqual(
                repository.update(updated, expected_revision=1), updated
            )
            with self.assertRaises(ScopeRevisionConflict):
                repository.update(updated.model_copy(update={"revision": 3}), expected_revision=1)
            confirmed = repository.confirm(draft_id, expected_revision=2)
            self.assertEqual(confirmed.companies[0].profile_id, profile_id)
            self.assertEqual(confirmed.companies[0].profile_version, 1)
            self.assertEqual(len(confirmed.companies[0].names), 1)
            self.assertEqual(repository.get_confirmed(confirmed.scope_revision_id), confirmed)
            self.assertEqual(repository.confirm(draft_id, expected_revision=2), confirmed)
            memory = repository.find_company_memory(unique_name)
            self.assertIsNotNone(memory)
            self.assertEqual(memory.profile_version, 1)
            self.assertEqual(memory.company.profile_id, profile_id)
            excluded_name = company_value.names[1].text
            self.assertIsNone(repository.find_company_memory(excluded_name))
            terminal_draft = repository.get(draft_id)
            self.assertEqual(terminal_draft.revision, 3)
            self.assertEqual(terminal_draft.status, ScopeDraftStatus.CONFIRMED)
            count = raw.execute(
                """
                SELECT count(*) AS count
                FROM landscape_v4_scope_draft_revisions
                WHERE draft_id=%s
                """,
                (draft_id,),
            ).fetchone()["count"]
            self.assertEqual(count, 3)
            profile_version_count = raw.execute(
                """
                SELECT count(*) AS count
                FROM landscape_v4_company_profile_versions
                WHERE profile_id=%s
                """,
                (profile_id,),
            ).fetchone()["count"]
            registry_count = raw.execute(
                """
                SELECT count(*) AS count
                FROM landscape_v4_company_name_registry
                WHERE profile_id=%s
                """,
                (profile_id,),
            ).fetchone()["count"]
            self.assertEqual(profile_version_count, 1)
            self.assertEqual(registry_count, 2)
        finally:
            raw.rollback()
        missing = raw.execute(
            "SELECT count(*) AS count FROM landscape_v4_scope_drafts WHERE draft_id=%s",
            (draft_id,),
        ).fetchone()["count"]
        raw.rollback()
        raw.close()
        self.assertEqual(missing, 0)


if __name__ == "__main__":
    unittest.main()
