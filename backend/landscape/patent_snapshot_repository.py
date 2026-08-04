from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from typing import Iterator

from pydantic import ValidationError

from .patent_snapshot import PatentSnapshot, PatentSnapshotSet


class PatentSnapshotPersistenceError(RuntimeError):
    pass


class PostgreSQLPatentSnapshotRepository:
    def __init__(self, dsn: str, *, connect=None):
        if not isinstance(dsn, str) or not dsn.strip():
            raise ValueError("PostgreSQL DSN must not be blank")
        self._dsn = dsn.strip()
        self._connect_factory = connect

    def put(self, result: PatentSnapshotSet) -> PatentSnapshotSet:
        result = PatentSnapshotSet.model_validate(result.model_dump(mode="json"))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT publication_id FROM landscape_v4_publications WHERE run_id=%s",
                (result.run_id,),
            ).fetchall()
            expected = {row["publication_id"] for row in rows}
            actual = {item.publication_id for item in result.snapshots}
            if actual != expected:
                raise PatentSnapshotPersistenceError(
                    "patent snapshots must exactly cover frozen publications"
                )
            for order, snapshot in enumerate(result.snapshots, start=1):
                self._insert_one(connection, result.run_id, snapshot, order)
            stored = self._load(connection, result.run_id)
            if stored != result:
                raise PatentSnapshotPersistenceError("patent snapshots are immutable")
            return stored

    def put_one(
        self,
        run_id: str,
        snapshot: PatentSnapshot,
        *,
        sort_order: int,
    ) -> PatentSnapshot:
        snapshot = PatentSnapshot.model_validate(snapshot.model_dump(mode="json"))
        if sort_order < 1:
            raise ValueError("snapshot sort order must be positive")
        with self._connect() as connection:
            self._insert_one(connection, run_id, snapshot, sort_order)
            stored = self._load_one(connection, run_id, snapshot.publication_id)
            if stored != snapshot:
                raise PatentSnapshotPersistenceError("patent snapshot is immutable")
            return stored

    def list(self, run_id: str) -> tuple[PatentSnapshot, ...]:
        with self._connect() as connection:
            return self._load_partial(connection, run_id)

    def get(self, run_id: str) -> PatentSnapshotSet:
        with self._connect() as connection:
            return self._load(connection, run_id)

    @staticmethod
    def _insert_one(connection, run_id: str, item: PatentSnapshot, sort_order: int) -> None:
        if connection.execute(
            "SELECT publication_id FROM landscape_v4_publications WHERE run_id=%s AND publication_id=%s",
            (run_id, item.publication_id),
        ).fetchone() is None:
            raise KeyError((run_id, item.publication_id))
        inserted = connection.execute(
            """
            INSERT INTO landscape_v4_patent_snapshots(
                run_id,publication_id,status,publication_number,application_number,
                family_id,title,priority_date,filing_date,publication_date,url,
                language,abstract_text,provider,failure_reason,content_hash,sort_order
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (run_id,publication_id) DO NOTHING RETURNING publication_id
            """,
            (
                run_id, item.publication_id, item.status.value,
                item.publication_number, item.application_number, item.family_id,
                item.title, item.priority_date, item.filing_date,
                item.publication_date, item.url, item.language,
                item.abstract_text, item.provider, item.failure_reason,
                item.content_hash, sort_order,
            ),
        ).fetchone()
        if inserted is not None and item.applicants:
            with connection.cursor() as cursor:
                cursor.executemany(
                    """
                    INSERT INTO landscape_v4_patent_snapshot_applicants(
                        run_id,publication_id,applicant_name,sort_order
                    ) VALUES (%s,%s,%s,%s)
                    """,
                    [
                        (run_id, item.publication_id, applicant, order)
                        for order, applicant in enumerate(item.applicants, start=1)
                    ],
                )

    @staticmethod
    def _load(connection, run_id: str) -> PatentSnapshotSet:
        snapshots = PostgreSQLPatentSnapshotRepository._load_partial(connection, run_id)
        expected_rows = connection.execute(
            "SELECT publication_id FROM landscape_v4_publications WHERE run_id=%s",
            (run_id,),
        ).fetchall()
        if {item.publication_id for item in snapshots} != {
            row["publication_id"] for row in expected_rows
        }:
            raise PatentSnapshotPersistenceError(
                "patent snapshots do not yet cover frozen publications"
            )
        return PatentSnapshotSet(run_id=run_id, snapshots=snapshots)

    @staticmethod
    def _load_partial(connection, run_id: str) -> tuple[PatentSnapshot, ...]:
        rows = connection.execute(
            """
            SELECT * FROM landscape_v4_patent_snapshots
            WHERE run_id=%s ORDER BY sort_order
            """,
            (run_id,),
        ).fetchall()
        applicant_rows = connection.execute(
            """
            SELECT publication_id,applicant_name,sort_order
            FROM landscape_v4_patent_snapshot_applicants
            WHERE run_id=%s ORDER BY publication_id,sort_order
            """,
            (run_id,),
        ).fetchall()
        applicants: dict[str, list[str]] = {}
        for row in applicant_rows:
            values = applicants.setdefault(row["publication_id"], [])
            if row["sort_order"] != len(values) + 1:
                raise PatentSnapshotPersistenceError("applicant order is not contiguous")
            values.append(row["applicant_name"])
        try:
            snapshots = tuple(
                PatentSnapshot(
                    publication_id=row["publication_id"],
                    status=row["status"],
                    publication_number=row["publication_number"],
                    application_number=row["application_number"],
                    family_id=row["family_id"],
                    title=row["title"],
                    applicants=tuple(applicants.pop(row["publication_id"], ())),
                    priority_date=_date(row["priority_date"]),
                    filing_date=_date(row["filing_date"]),
                    publication_date=_date(row["publication_date"]),
                    url=row["url"],
                    language=row["language"],
                    abstract_text=row["abstract_text"],
                    provider=row["provider"],
                    failure_reason=row["failure_reason"],
                    content_hash=row["content_hash"],
                )
                for row in rows
            )
            if applicants:
                raise PatentSnapshotPersistenceError("snapshot applicant has no parent")
            return snapshots
        except (ValidationError, TypeError, ValueError) as exc:
            raise PatentSnapshotPersistenceError(
                "stored patent snapshots failed validation"
            ) from exc

    @staticmethod
    def _load_one(connection, run_id: str, publication_id: str) -> PatentSnapshot:
        matches = tuple(
            item
            for item in PostgreSQLPatentSnapshotRepository._load_partial(connection, run_id)
            if item.publication_id == publication_id
        )
        if not matches:
            raise KeyError((run_id, publication_id))
        return matches[0]

    @contextmanager
    def _connect(self) -> Iterator[object]:
        if self._connect_factory is not None:
            with self._connect_factory() as connection:
                yield connection
            return
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(
            self._dsn, row_factory=dict_row, connect_timeout=10
        ) as connection:
            yield connection


def _date(value) -> date | None:
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


__all__ = ["PatentSnapshotPersistenceError", "PostgreSQLPatentSnapshotRepository"]
