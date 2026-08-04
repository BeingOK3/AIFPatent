from __future__ import annotations

import time
from contextlib import contextmanager
from datetime import date
from typing import Iterator

from pydantic import ValidationError

from .publication_freeze import FrozenPublication, FrozenPublicationSet


class PublicationPersistenceError(RuntimeError):
    pass


class PostgreSQLPublicationRepository:
    def __init__(self, dsn: str, *, connect=None):
        if not isinstance(dsn, str) or not dsn.strip():
            raise ValueError("PostgreSQL DSN must not be blank")
        self._dsn = dsn.strip()
        self._connect_factory = connect

    def put(self, frozen: FrozenPublicationSet) -> FrozenPublicationSet:
        frozen = FrozenPublicationSet.model_validate(frozen.model_dump(mode="json"))
        timestamp = int(time.time() * 1000)
        with self._connect() as connection:
            if connection.execute(
                "SELECT run_id FROM landscape_v4_runs WHERE run_id=%s FOR UPDATE",
                (frozen.run_id,),
            ).fetchone() is None:
                raise KeyError(frozen.run_id)
            inserted = connection.execute(
                """
                INSERT INTO landscape_v4_publication_sets(
                    run_id,freeze_hash,publication_count,analysis_unit_count,created_at
                ) VALUES (%s,%s,%s,%s,%s)
                ON CONFLICT (run_id) DO NOTHING RETURNING run_id
                """,
                (
                    frozen.run_id, frozen.freeze_hash, frozen.publication_count,
                    frozen.analysis_unit_count, timestamp,
                ),
            ).fetchone()
            if inserted is not None:
                with connection.cursor() as cursor:
                    cursor.executemany(
                        """
                        INSERT INTO landscape_v4_publications(
                            run_id,publication_id,publication_identity,
                            publication_number,title,url,publication_date,family_id,
                            provider,content_hash,sort_order
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        """,
                        [
                            (
                                frozen.run_id, item.publication_id,
                                item.publication_identity, item.publication_number,
                                item.title, item.url, item.publication_date,
                                item.family_id, item.provider, item.content_hash, order,
                            )
                            for order, item in enumerate(frozen.publications, start=1)
                        ],
                    )
                    cursor.executemany(
                        """
                        INSERT INTO landscape_v4_publication_sources(
                            run_id,publication_id,query_id
                        ) VALUES (%s,%s,%s)
                        """,
                        [
                            (frozen.run_id, item.publication_id, query_id)
                            for item in frozen.publications
                            for query_id in item.source_queries
                        ],
                    )
            stored = self._load(connection, frozen.run_id)
            if stored != frozen:
                raise PublicationPersistenceError(
                    "Run already has a different immutable publication set"
                )
            return stored

    def get(self, run_id: str) -> FrozenPublicationSet:
        with self._connect() as connection:
            return self._load(connection, run_id)

    @staticmethod
    def _load(connection, run_id: str) -> FrozenPublicationSet:
        manifest = connection.execute(
            "SELECT * FROM landscape_v4_publication_sets WHERE run_id=%s",
            (run_id,),
        ).fetchone()
        if manifest is None:
            raise KeyError(run_id)
        rows = connection.execute(
            "SELECT * FROM landscape_v4_publications WHERE run_id=%s ORDER BY sort_order",
            (run_id,),
        ).fetchall()
        sources = connection.execute(
            "SELECT publication_id,query_id FROM landscape_v4_publication_sources WHERE run_id=%s ORDER BY publication_id,query_id",
            (run_id,),
        ).fetchall()
        by_publication: dict[str, list[str]] = {}
        for source in sources:
            by_publication.setdefault(source["publication_id"], []).append(source["query_id"])
        try:
            publications = tuple(
                FrozenPublication(
                    publication_id=row["publication_id"],
                    publication_identity=row["publication_identity"],
                    publication_number=row["publication_number"],
                    title=row["title"],
                    url=row["url"],
                    publication_date=_date(row["publication_date"]),
                    family_id=row["family_id"],
                    source_queries=tuple(by_publication.pop(row["publication_id"], ())),
                    provider=row["provider"],
                    content_hash=row["content_hash"],
                )
                for expected, row in enumerate(rows, start=1)
                if _contiguous(row, expected)
            )
            if by_publication:
                raise PublicationPersistenceError("publication source has no member")
            return FrozenPublicationSet(
                run_id=manifest["run_id"],
                publications=publications,
                publication_count=manifest["publication_count"],
                analysis_unit_count=manifest["analysis_unit_count"],
                freeze_hash=manifest["freeze_hash"],
            )
        except (ValidationError, TypeError, ValueError) as exc:
            raise PublicationPersistenceError(
                "stored publication set failed validation"
            ) from exc

    @contextmanager
    def _connect(self) -> Iterator[object]:
        if self._connect_factory is not None:
            with self._connect_factory() as connection:
                yield connection
            return
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(self._dsn, row_factory=dict_row, connect_timeout=10) as connection:
            yield connection


def _contiguous(row, expected: int) -> bool:
    if row["sort_order"] != expected:
        raise PublicationPersistenceError("publication order is not contiguous")
    return True


def _date(value) -> date | None:
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


__all__ = ["PostgreSQLPublicationRepository", "PublicationPersistenceError"]
