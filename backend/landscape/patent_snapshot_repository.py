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
            existing = connection.execute(
                "SELECT publication_id FROM landscape_v4_patent_snapshots WHERE run_id=%s",
                (result.run_id,),
            ).fetchall()
            if not existing:
                self._insert(connection, result)
            stored = self._load(connection, result.run_id)
            if stored != result:
                raise PatentSnapshotPersistenceError("patent snapshots are immutable")
            return stored

    def get(self, run_id: str) -> PatentSnapshotSet:
        with self._connect() as connection:
            return self._load(connection, run_id)

    @staticmethod
    def _insert(connection, result: PatentSnapshotSet) -> None:
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO landscape_v4_patent_snapshots(
                    run_id,publication_id,status,publication_number,application_number,
                    family_id,title,priority_date,filing_date,publication_date,url,
                    language,abstract_text,provider,failure_reason,content_hash,sort_order
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                [
                    (
                        result.run_id,
                        item.publication_id,
                        item.status.value,
                        item.publication_number,
                        item.application_number,
                        item.family_id,
                        item.title,
                        item.priority_date,
                        item.filing_date,
                        item.publication_date,
                        item.url,
                        item.language,
                        item.abstract_text,
                        item.provider,
                        item.failure_reason,
                        item.content_hash,
                        order,
                    )
                    for order, item in enumerate(result.snapshots, start=1)
                ],
            )
            cursor.executemany(
                """
                INSERT INTO landscape_v4_patent_snapshot_applicants(
                    run_id,publication_id,applicant_name,sort_order
                ) VALUES (%s,%s,%s,%s)
                """,
                [
                    (result.run_id, item.publication_id, applicant, order)
                    for item in result.snapshots
                    for order, applicant in enumerate(item.applicants, start=1)
                ],
            )

    @staticmethod
    def _load(connection, run_id: str) -> PatentSnapshotSet:
        rows = connection.execute(
            """
            SELECT * FROM landscape_v4_patent_snapshots
            WHERE run_id=%s ORDER BY sort_order
            """,
            (run_id,),
        ).fetchall()
        if not rows:
            raise KeyError(run_id)
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
                for expected, row in enumerate(rows, start=1)
                if _contiguous(row, expected)
            )
            if applicants:
                raise PatentSnapshotPersistenceError("snapshot applicant has no parent")
            return PatentSnapshotSet(run_id=run_id, snapshots=snapshots)
        except (ValidationError, TypeError, ValueError) as exc:
            raise PatentSnapshotPersistenceError(
                "stored patent snapshots failed validation"
            ) from exc

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


def _contiguous(row, expected: int) -> bool:
    if row["sort_order"] != expected:
        raise PatentSnapshotPersistenceError("snapshot order is not contiguous")
    return True


__all__ = ["PatentSnapshotPersistenceError", "PostgreSQLPatentSnapshotRepository"]
