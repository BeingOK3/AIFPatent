from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from .family_resolution import AnalysisUnit, FamilyResolution


FAMILY_ALGORITHM_VERSION = "conservative-family/1"


class FamilyPersistenceError(RuntimeError):
    pass


class PostgreSQLFamilyRepository:
    def __init__(self, dsn: str, *, connect=None):
        if not isinstance(dsn, str) or not dsn.strip():
            raise ValueError("PostgreSQL DSN must not be blank")
        self._dsn = dsn.strip()
        self._connect_factory = connect

    def put(self, run_id: str, resolution: FamilyResolution) -> FamilyResolution:
        resolution = FamilyResolution.model_validate(resolution.model_dump(mode="json"))
        with self._connect() as connection:
            publications = connection.execute(
                """
                SELECT publication_id FROM landscape_v4_publications
                WHERE run_id=%s ORDER BY publication_id
                """,
                (run_id,),
            ).fetchall()
            expected = {row["publication_id"] for row in publications}
            actual = {
                publication_id
                for unit in resolution.analysis_units
                for publication_id in unit.member_publication_ids
            }
            if expected != actual:
                raise FamilyPersistenceError(
                    "family resolution must exactly partition frozen publications"
                )
            inserted = connection.execute(
                """
                INSERT INTO landscape_v4_family_manifests(
                    run_id,algorithm_version,publication_count,
                    analysis_unit_count,resolution_hash
                ) VALUES (%s,%s,%s,%s,%s)
                ON CONFLICT (run_id) DO NOTHING RETURNING run_id
                """,
                (
                    run_id,
                    FAMILY_ALGORITHM_VERSION,
                    resolution.publication_count,
                    resolution.analysis_unit_count,
                    resolution.resolution_hash,
                ),
            ).fetchone()
            if inserted is not None:
                with connection.cursor() as cursor:
                    cursor.executemany(
                        """
                        INSERT INTO landscape_v4_analysis_units(
                            run_id,analysis_unit_id,merge_basis,sort_order
                        ) VALUES (%s,%s,%s,%s)
                        """,
                        [
                            (
                                run_id,
                                unit.analysis_unit_id,
                                unit.merge_basis,
                                index,
                            )
                            for index, unit in enumerate(
                                resolution.analysis_units,
                                start=1,
                            )
                        ],
                    )
                    cursor.executemany(
                        """
                        INSERT INTO landscape_v4_analysis_unit_members(
                            run_id,analysis_unit_id,publication_id,sort_order
                        ) VALUES (%s,%s,%s,%s)
                        """,
                        [
                            (
                                run_id,
                                unit.analysis_unit_id,
                                publication_id,
                                index,
                            )
                            for unit in resolution.analysis_units
                            for index, publication_id in enumerate(
                                unit.member_publication_ids,
                                start=1,
                            )
                        ],
                    )
            stored = self._load(connection, run_id)
            if stored != resolution:
                raise FamilyPersistenceError("family resolution is immutable")
            return stored

    def get(self, run_id: str) -> FamilyResolution:
        with self._connect() as connection:
            return self._load(connection, run_id)

    @staticmethod
    def _load(connection, run_id: str) -> FamilyResolution:
        manifest = connection.execute(
            "SELECT * FROM landscape_v4_family_manifests WHERE run_id=%s",
            (run_id,),
        ).fetchone()
        if manifest is None:
            raise KeyError(run_id)
        if manifest["algorithm_version"] != FAMILY_ALGORITHM_VERSION:
            raise FamilyPersistenceError("unknown family algorithm version")
        rows = connection.execute(
            """
            SELECT * FROM landscape_v4_analysis_units
            WHERE run_id=%s ORDER BY sort_order
            """,
            (run_id,),
        ).fetchall()
        units = []
        for expected_order, row in enumerate(rows, start=1):
            if row["sort_order"] != expected_order:
                raise FamilyPersistenceError("analysis unit order is not contiguous")
            members = connection.execute(
                """
                SELECT publication_id,sort_order
                FROM landscape_v4_analysis_unit_members
                WHERE run_id=%s AND analysis_unit_id=%s ORDER BY sort_order
                """,
                (run_id, row["analysis_unit_id"]),
            ).fetchall()
            if any(
                member["sort_order"] != index
                for index, member in enumerate(members, start=1)
            ):
                raise FamilyPersistenceError("analysis unit member order is not contiguous")
            units.append(
                AnalysisUnit(
                    analysis_unit_id=row["analysis_unit_id"],
                    member_publication_ids=tuple(
                        member["publication_id"] for member in members
                    ),
                    merge_basis=row["merge_basis"],
                )
            )
        try:
            return FamilyResolution(
                analysis_units=tuple(units),
                publication_count=manifest["publication_count"],
                analysis_unit_count=manifest["analysis_unit_count"],
                resolution_hash=manifest["resolution_hash"],
            )
        except ValueError as exc:
            raise FamilyPersistenceError("stored family resolution failed validation") from exc

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


__all__ = [
    "FAMILY_ALGORITHM_VERSION",
    "FamilyPersistenceError",
    "PostgreSQLFamilyRepository",
]
