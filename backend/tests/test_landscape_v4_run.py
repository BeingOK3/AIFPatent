from __future__ import annotations

import unittest
import os
import uuid
from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace

from landscape.run_repository import (
    LandscapeRunPersistenceError,
    PostgreSQLLandscapeRunRepository,
)
from landscape.scope import (
    CandidateSource,
    CandidateStatus,
    CompanyNameRelation,
    CompanyScopeDraft,
    LandscapeInputMode,
    NameLanguage,
    ScopeDraft,
    ScopeDraftStatus,
    make_company_name_candidate,
    make_company_profile_id,
    make_scope_draft_id,
)
from landscape.scope_repository import PostgreSQLScopeDraftRepository
from landscape.v4_run import LandscapeRunStatus


class Cursor:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows if rows is not None else ([] if row is None else [row])

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class RunConnection:
    def __init__(self):
        self.rows = {}

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        if normalized.startswith("INSERT INTO landscape_v4_runs"):
            run_id = params[0]
            if run_id in self.rows:
                return Cursor()
            keys = (
                "run_id", "scope_revision_id", "scope_revision_hash",
                "taxonomy_version", "taxonomy_hash", "status", "mode",
                "publication_start", "publication_end", "workflow_version",
                "created_at", "updated_at",
            )
            self.rows[run_id] = dict(zip(keys, params, strict=True))
            return Cursor({"run_id": run_id})
        if "WHERE run_id=%s" in normalized:
            return Cursor(self.rows.get(params[0]))
        if "ORDER BY created_at DESC" in normalized:
            rows = sorted(
                self.rows.values(),
                key=lambda row: (row["created_at"], row["run_id"]),
                reverse=True,
            )[: params[0]]
            return Cursor(rows=rows)
        raise AssertionError(normalized)


class ScopeStub:
    def __init__(self):
        self.scope = SimpleNamespace(
            scope_revision_id="SCR-0000000000000001",
            scope_revision_hash="1" * 64,
            mode=LandscapeInputMode.COMPANY_AND_TECHNOLOGY,
            publication_start=date(2000, 1, 1),
            publication_end=date(2026, 12, 31),
        )

    def get_confirmed(self, scope_revision_id):
        if scope_revision_id != self.scope.scope_revision_id:
            raise KeyError(scope_revision_id)
        return self.scope


class TaxonomyStub:
    def __init__(self):
        self.taxonomy = SimpleNamespace(
            taxonomy_version="landscape-taxonomy/fixture",
            taxonomy_hash="2" * 64,
        )

    def get(self, taxonomy_version):
        if taxonomy_version != self.taxonomy.taxonomy_version:
            raise KeyError(taxonomy_version)
        return self.taxonomy


def repository_fixture():
    connection = RunConnection()

    @contextmanager
    def connect():
        yield connection

    repository = PostgreSQLLandscapeRunRepository(
        "postgresql://fixture",
        ScopeStub(),
        TaxonomyStub(),
        connect=connect,
    )
    return repository, connection


class LandscapeV4RunRepositoryTests(unittest.TestCase):
    def test_run_binds_only_confirmed_scope_and_current_taxonomy(self) -> None:
        repository, _connection = repository_fixture()
        run = repository.create(
            scope_revision_id="SCR-0000000000000001",
            taxonomy_version="landscape-taxonomy/fixture",
            run_id="LRN-0000000000000001",
        )
        self.assertEqual(run.status, LandscapeRunStatus.PLANNING)
        self.assertEqual(run.workflow_version, "landscape-v4/1.0.0")
        self.assertEqual(run.publication_start, date(2000, 1, 1))
        self.assertEqual(run.publication_end, date(2026, 12, 31))
        self.assertEqual(repository.get(run.run_id), run)
        self.assertEqual(repository.list(), (run,))

    def test_unconfirmed_or_unknown_scope_cannot_create_run(self) -> None:
        repository, connection = repository_fixture()
        with self.assertRaisesRegex(LandscapeRunPersistenceError, "confirmed"):
            repository.create(
                scope_revision_id="SCR-0000000000000099",
                taxonomy_version="landscape-taxonomy/fixture",
            )
        self.assertEqual(connection.rows, {})

    def test_tampered_scope_or_taxonomy_binding_fails_closed(self) -> None:
        repository, connection = repository_fixture()
        run = repository.create(
            scope_revision_id="SCR-0000000000000001",
            taxonomy_version="landscape-taxonomy/fixture",
            run_id="LRN-0000000000000002",
        )
        connection.rows[run.run_id]["scope_revision_hash"] = "0" * 64
        with self.assertRaisesRegex(LandscapeRunPersistenceError, "scope hash"):
            repository.get(run.run_id)


@unittest.skipUnless(
    os.environ.get("AIFPATENT_RUN_SCOPE_INTEGRATION") == "1",
    "set AIFPATENT_RUN_SCOPE_INTEGRATION=1 to test real PostgreSQL v4 Runs",
)
class LandscapeV4RunPostgreSQLIntegrationTests(unittest.TestCase):
    def test_confirmed_scope_creates_replayable_run_and_rolls_back_cleanly(self) -> None:
        import psycopg
        from psycopg.rows import dict_row

        raw = psycopg.connect(
            os.environ["AIFPATENT_POSTGRES_DSN"],
            row_factory=dict_row,
            connect_timeout=10,
        )

        @contextmanager
        def connect():
            yield raw

        taxonomy_row = raw.execute(
            """
            SELECT taxonomy_version,taxonomy_hash
            FROM landscape_taxonomy_versions
            ORDER BY created_at DESC,taxonomy_version DESC
            LIMIT 1
            """
        ).fetchone()
        self.assertIsNotNone(taxonomy_row)
        taxonomy = SimpleNamespace(**taxonomy_row)

        class RealTaxonomyStub:
            def get(self, taxonomy_version):
                if taxonomy_version != taxonomy.taxonomy_version:
                    raise KeyError(taxonomy_version)
                return taxonomy

        unique_name = f"v4-run-integration-{uuid.uuid4().hex}"
        profile_id = make_company_profile_id(unique_name)
        draft = ScopeDraft(
            draft_id=make_scope_draft_id(uuid.uuid4().hex),
            revision=1,
            status=ScopeDraftStatus.AWAITING_CONFIRMATION,
            publication_start=date(1999, 1, 1),
            publication_end=date(2026, 12, 31),
            companies=(
                CompanyScopeDraft(
                    profile_id=profile_id,
                    display_name=unique_name,
                    input_name=unique_name,
                    names=(
                        make_company_name_candidate(
                            profile_id=profile_id,
                            text=unique_name,
                            language=NameLanguage.EN,
                            relation_type=CompanyNameRelation.LEGAL_NAME,
                            source=CandidateSource.USER_INPUT,
                            status=CandidateStatus.ACTIVE,
                        ),
                    ),
                ),
            ),
        )
        scope_repository = PostgreSQLScopeDraftRepository(
            "postgresql://integration", connect=connect
        )
        run_repository = PostgreSQLLandscapeRunRepository(
            "postgresql://integration",
            scope_repository,
            RealTaxonomyStub(),
            connect=connect,
        )
        try:
            scope_repository.create(draft)
            confirmed = scope_repository.confirm(
                draft.draft_id,
                expected_revision=1,
            )
            run = run_repository.create(
                scope_revision_id=confirmed.scope_revision_id,
                taxonomy_version=taxonomy.taxonomy_version,
            )
            self.assertEqual(run_repository.get(run.run_id), run)
            self.assertEqual(run.scope_revision_hash, confirmed.scope_revision_hash)
            self.assertEqual(run.publication_start, date(1999, 1, 1))
            raw.rollback()
            count = raw.execute(
                "SELECT count(*) AS count FROM landscape_v4_runs WHERE run_id=%s",
                (run.run_id,),
            ).fetchone()["count"]
            self.assertEqual(count, 0)
        finally:
            raw.rollback()
            raw.close()


if __name__ == "__main__":
    unittest.main()
