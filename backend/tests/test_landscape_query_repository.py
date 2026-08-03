from __future__ import annotations

import unittest
from contextlib import contextmanager

from landscape.query_planning import build_query_plan
from landscape.query_repository import (
    PostgreSQLQueryPlanRepository,
    QueryPlanPersistenceError,
)
from landscape.scope import NameLanguage
from tests.test_landscape_query_planning import confirmed_scope


class Cursor:
    def __init__(self, connection, row=None, rows=None):
        self.connection = connection
        self.row = row
        self.rows = rows if rows is not None else ([] if row is None else [row])

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def executemany(self, sql, values):
        normalized = " ".join(sql.split())
        if normalized.startswith("INSERT INTO landscape_v4_search_queries"):
            keys = (
                "run_id", "query_id", "query_hash", "mode",
                "company_profile_id", "company_name_id", "company_name",
                "publication_start", "publication_end", "query_text",
                "sort_order", "created_at",
            )
            self.connection.queries.extend(
                dict(zip(keys, row, strict=True)) for row in values
            )
            return
        if normalized.startswith("INSERT INTO landscape_v4_search_query_terms"):
            keys = ("run_id", "query_id", "term_id", "term_text", "sort_order")
            self.connection.terms.extend(
                dict(zip(keys, row, strict=True)) for row in values
            )
            return
        raise AssertionError(normalized)


class QueryConnection:
    def __init__(self, plan):
        self.run = {
            "run_id": "LRN-0000000000000001",
            "scope_revision_id": plan.scope_revision_id,
            "scope_revision_hash": plan.scope_revision_hash,
            "status": "PLANNING",
        }
        self.manifest = None
        self.queries = []
        self.terms = []

    def cursor(self):
        return Cursor(self)

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        if "FROM landscape_v4_runs" in normalized and "FOR UPDATE" in normalized:
            return Cursor(self, self.run if params[0] == self.run["run_id"] else None)
        if normalized.startswith("INSERT INTO landscape_v4_query_plans"):
            if self.manifest is not None:
                return Cursor(self)
            keys = (
                "run_id", "scope_revision_id", "scope_revision_hash",
                "plan_hash", "query_count", "created_at",
            )
            self.manifest = dict(zip(keys, params, strict=True))
            return Cursor(self, {"run_id": params[0]})
        if "FROM landscape_v4_query_plans" in normalized:
            return Cursor(self, self.manifest)
        if "FROM landscape_v4_search_queries" in normalized:
            return Cursor(
                self,
                rows=sorted(self.queries, key=lambda row: row["sort_order"]),
            )
        if "FROM landscape_v4_search_query_terms" in normalized:
            return Cursor(
                self,
                rows=sorted(
                    self.terms,
                    key=lambda row: (row["query_id"], row["sort_order"]),
                ),
            )
        if normalized.startswith("UPDATE landscape_v4_runs"):
            if self.run["status"] == "PLANNING":
                self.run["status"] = "ESTIMATING"
            return Cursor(self)
        raise AssertionError(normalized)


def repository_fixture():
    scope = confirmed_scope(
        companies=(("华为", ("华为", "Huawei")),),
        technology_terms=(
            ("液冷", NameLanguage.ZH),
            ("liquid cooling", NameLanguage.EN),
        ),
    )
    plan = build_query_plan(scope, max_terms_per_group=1)
    connection = QueryConnection(plan)

    @contextmanager
    def connect():
        yield connection

    repository = PostgreSQLQueryPlanRepository(
        "postgresql://fixture",
        connect=connect,
    )
    return repository, connection, plan


class LandscapeQueryPlanRepositoryTests(unittest.TestCase):
    def test_plan_is_relational_idempotent_and_advances_run(self) -> None:
        repository, connection, plan = repository_fixture()
        self.assertEqual(repository.put(connection.run["run_id"], plan), plan)
        self.assertEqual(repository.put(connection.run["run_id"], plan), plan)
        self.assertEqual(connection.run["status"], "ESTIMATING")
        self.assertEqual(len(connection.queries), len(plan.queries))
        self.assertEqual(
            len(connection.terms),
            sum(len(query.terms) for query in plan.queries),
        )
        self.assertEqual(repository.get(connection.run["run_id"]), plan)

    def test_scope_mismatch_fails_before_any_plan_rows(self) -> None:
        repository, connection, plan = repository_fixture()
        connection.run["scope_revision_hash"] = "0" * 64
        with self.assertRaisesRegex(QueryPlanPersistenceError, "Run scope"):
            repository.put(connection.run["run_id"], plan)
        self.assertIsNone(connection.manifest)

    def test_tampered_query_or_manifest_hash_fails_closed(self) -> None:
        repository, connection, plan = repository_fixture()
        repository.put(connection.run["run_id"], plan)
        connection.queries[0]["query_text"] += " tampered"
        with self.assertRaisesRegex(QueryPlanPersistenceError, "query text"):
            repository.get(connection.run["run_id"])


if __name__ == "__main__":
    unittest.main()
