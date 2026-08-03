from __future__ import annotations

import time
from contextlib import contextmanager
from datetime import date
from typing import Iterator

from pydantic import ValidationError

from .query_planning import (
    V4QueryPlan,
    V4SearchQuery,
    validate_query_plan_integrity,
)


class QueryPlanPersistenceError(RuntimeError):
    pass


class PostgreSQLQueryPlanRepository:
    def __init__(self, dsn: str, *, connect=None):
        if not isinstance(dsn, str) or not dsn.strip():
            raise ValueError("PostgreSQL DSN must not be blank")
        self._dsn = dsn.strip()
        self._connect_factory = connect

    def put(self, run_id: str, plan: V4QueryPlan) -> V4QueryPlan:
        plan = V4QueryPlan.model_validate(plan.model_dump(mode="json"))
        timestamp = int(time.time() * 1000)
        with self._connect() as connection:
            run = connection.execute(
                """
                SELECT run_id,scope_revision_id,scope_revision_hash,status
                FROM landscape_v4_runs WHERE run_id=%s FOR UPDATE
                """,
                (run_id,),
            ).fetchone()
            if run is None:
                raise KeyError(run_id)
            if (
                run["scope_revision_id"] != plan.scope_revision_id
                or run["scope_revision_hash"] != plan.scope_revision_hash
            ):
                raise QueryPlanPersistenceError("query plan does not match Run scope")
            inserted = connection.execute(
                """
                INSERT INTO landscape_v4_query_plans(
                    run_id,scope_revision_id,scope_revision_hash,plan_hash,
                    query_count,created_at
                ) VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (run_id) DO NOTHING
                RETURNING run_id
                """,
                (
                    run_id, plan.scope_revision_id, plan.scope_revision_hash,
                    plan.plan_hash, len(plan.queries), timestamp,
                ),
            ).fetchone()
            if inserted is not None:
                with connection.cursor() as cursor:
                    cursor.executemany(
                        """
                        INSERT INTO landscape_v4_search_queries(
                            run_id,query_id,query_hash,mode,company_profile_id,
                            company_name_id,company_name,publication_start,
                            publication_end,query_text,sort_order,created_at
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        """,
                        [
                            (
                                run_id, query.query_id, query.query_hash,
                                query.mode.value, query.company_profile_id,
                                query.company_name_id, query.company_name,
                                query.publication_start, query.publication_end,
                                query.query_text, order, timestamp,
                            )
                            for order, query in enumerate(plan.queries, start=1)
                        ],
                    )
                    cursor.executemany(
                        """
                        INSERT INTO landscape_v4_search_query_terms(
                            run_id,query_id,term_id,term_text,sort_order
                        ) VALUES (%s,%s,%s,%s,%s)
                        """,
                        [
                            (run_id, query.query_id, term_id, term, order)
                            for query in plan.queries
                            for order, (term_id, term) in enumerate(
                                zip(query.term_ids, query.terms, strict=True),
                                start=1,
                            )
                        ],
                    )
            stored = self._load(connection, run_id)
            if stored != plan:
                raise QueryPlanPersistenceError(
                    "Run already has a different immutable query plan"
                )
            if run["status"] == "PLANNING":
                connection.execute(
                    """
                    UPDATE landscape_v4_runs
                    SET status='ESTIMATING',updated_at=%s
                    WHERE run_id=%s AND status='PLANNING'
                    """,
                    (timestamp, run_id),
                )
            elif run["status"] != "ESTIMATING":
                raise QueryPlanPersistenceError(
                    "query plan can only be attached while Run is PLANNING or ESTIMATING"
                )
            return stored

    def get(self, run_id: str) -> V4QueryPlan:
        with self._connect() as connection:
            return self._load(connection, run_id)

    @staticmethod
    def _load(connection, run_id: str) -> V4QueryPlan:
        manifest = connection.execute(
            """
            SELECT run_id,scope_revision_id,scope_revision_hash,plan_hash,
                   query_count,created_at
            FROM landscape_v4_query_plans WHERE run_id=%s
            """,
            (run_id,),
        ).fetchone()
        if manifest is None:
            raise KeyError(run_id)
        rows = connection.execute(
            """
            SELECT run_id,query_id,query_hash,mode,company_profile_id,
                   company_name_id,company_name,publication_start,publication_end,
                   query_text,sort_order
            FROM landscape_v4_search_queries
            WHERE run_id=%s ORDER BY sort_order
            """,
            (run_id,),
        ).fetchall()
        terms = connection.execute(
            """
            SELECT run_id,query_id,term_id,term_text,sort_order
            FROM landscape_v4_search_query_terms
            WHERE run_id=%s ORDER BY query_id,sort_order
            """,
            (run_id,),
        ).fetchall()
        by_query: dict[str, list[dict]] = {}
        for term in terms:
            by_query.setdefault(term["query_id"], []).append(term)
        queries = []
        try:
            for expected_order, row in enumerate(rows, start=1):
                if row["sort_order"] != expected_order:
                    raise QueryPlanPersistenceError("stored query order is not contiguous")
                query_terms = by_query.pop(row["query_id"], [])
                if any(
                    term["sort_order"] != order
                    for order, term in enumerate(query_terms, start=1)
                ):
                    raise QueryPlanPersistenceError("stored query term order is not contiguous")
                queries.append(
                    V4SearchQuery(
                        query_id=row["query_id"],
                        query_hash=row["query_hash"],
                        scope_revision_id=manifest["scope_revision_id"],
                        mode=row["mode"],
                        company_profile_id=row["company_profile_id"],
                        company_name_id=row["company_name_id"],
                        company_name=row["company_name"],
                        term_ids=tuple(term["term_id"] for term in query_terms),
                        terms=tuple(term["term_text"] for term in query_terms),
                        publication_start=_date(row["publication_start"]),
                        publication_end=_date(row["publication_end"]),
                        query_text=row["query_text"],
                    )
                )
            if by_query:
                raise QueryPlanPersistenceError("stored query terms have no query")
            plan = V4QueryPlan(
                scope_revision_id=manifest["scope_revision_id"],
                scope_revision_hash=manifest["scope_revision_hash"],
                plan_hash=manifest["plan_hash"],
                queries=tuple(queries),
            )
        except (ValidationError, TypeError, ValueError) as exc:
            raise QueryPlanPersistenceError("stored query plan failed validation") from exc
        if manifest["query_count"] != len(plan.queries):
            raise QueryPlanPersistenceError("stored query plan count mismatch")
        try:
            validate_query_plan_integrity(plan)
        except ValueError as exc:
            raise QueryPlanPersistenceError(f"stored {exc}") from exc
        return plan

    @contextmanager
    def _connect(self) -> Iterator[object]:
        if self._connect_factory is not None:
            with self._connect_factory() as connection:
                yield connection
            return
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover
            raise QueryPlanPersistenceError(
                "psycopg is required for query plan persistence"
            ) from exc
        with psycopg.connect(
            self._dsn, row_factory=dict_row, connect_timeout=10
        ) as connection:
            yield connection


def _date(value) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


__all__ = ["PostgreSQLQueryPlanRepository", "QueryPlanPersistenceError"]
