from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from pydantic import ValidationError

from .organization_assignment import (
    ORGANIZATION_POLICY_VERSION,
    Organization,
    OrganizationAssignmentSet,
    PublicationOrganizationAssignment,
)


class OrganizationPersistenceError(RuntimeError):
    pass


class PostgreSQLOrganizationRepository:
    def __init__(self, dsn: str, *, connect=None):
        if not isinstance(dsn, str) or not dsn.strip():
            raise ValueError("PostgreSQL DSN must not be blank")
        self._dsn = dsn.strip()
        self._connect_factory = connect

    def put(self, result: OrganizationAssignmentSet) -> OrganizationAssignmentSet:
        result = OrganizationAssignmentSet.model_validate(result.model_dump(mode="json"))
        with self._connect() as connection:
            publication_rows = connection.execute(
                "SELECT publication_id FROM landscape_v4_publications WHERE run_id=%s",
                (result.run_id,),
            ).fetchall()
            expected = {row["publication_id"] for row in publication_rows}
            actual = {item.publication_id for item in result.assignments}
            if actual != expected:
                raise OrganizationPersistenceError(
                    "organization assignments must exactly cover frozen publications"
                )
            inserted = connection.execute(
                """
                INSERT INTO landscape_v4_organization_manifests(
                    run_id,policy_version,organization_count,assignment_count
                ) VALUES (%s,%s,%s,%s)
                ON CONFLICT (run_id) DO NOTHING RETURNING run_id
                """,
                (
                    result.run_id,
                    result.policy_version,
                    len(result.organizations),
                    len(result.assignments),
                ),
            ).fetchone()
            if inserted is not None:
                self._insert(connection, result)
            stored = self._load(connection, result.run_id)
            if stored != result:
                raise OrganizationPersistenceError("organization assignments are immutable")
            return stored

    def get(self, run_id: str) -> OrganizationAssignmentSet:
        with self._connect() as connection:
            return self._load(connection, run_id)

    @staticmethod
    def _insert(connection, result: OrganizationAssignmentSet) -> None:
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO landscape_v4_organizations(
                    run_id,organization_id,display_name,normalized_name,
                    organization_type,source_profile_id,sort_order
                ) VALUES (%s,%s,%s,%s,%s,%s,%s)
                """,
                [
                    (
                        result.run_id,
                        item.organization_id,
                        item.display_name,
                        item.normalized_name,
                        item.organization_type.value,
                        item.source_profile_id,
                        order,
                    )
                    for order, item in enumerate(result.organizations, start=1)
                ],
            )
            cursor.executemany(
                """
                INSERT INTO landscape_v4_organization_names(
                    run_id,organization_id,observed_name,sort_order
                ) VALUES (%s,%s,%s,%s)
                """,
                [
                    (result.run_id, item.organization_id, name, order)
                    for item in result.organizations
                    for order, name in enumerate(item.observed_names, start=1)
                ],
            )
            cursor.executemany(
                """
                INSERT INTO landscape_v4_publication_organizations(
                    run_id,publication_id,primary_organization_id,sort_order
                ) VALUES (%s,%s,%s,%s)
                """,
                [
                    (
                        result.run_id,
                        item.publication_id,
                        item.primary_organization_id,
                        order,
                    )
                    for order, item in enumerate(result.assignments, start=1)
                ],
            )
            cursor.executemany(
                """
                INSERT INTO landscape_v4_publication_co_organizations(
                    run_id,publication_id,organization_id,sort_order
                ) VALUES (%s,%s,%s,%s)
                """,
                [
                    (result.run_id, item.publication_id, organization_id, order)
                    for item in result.assignments
                    for order, organization_id in enumerate(
                        item.co_organization_ids, start=1
                    )
                ],
            )
            cursor.executemany(
                """
                INSERT INTO landscape_v4_publication_applicants(
                    run_id,publication_id,observed_name,is_unconfirmed,sort_order
                ) VALUES (%s,%s,%s,%s,%s)
                """,
                [
                    (
                        result.run_id,
                        item.publication_id,
                        name,
                        name in item.unconfirmed_applicants,
                        order,
                    )
                    for item in result.assignments
                    for order, name in enumerate(item.observed_applicants, start=1)
                ],
            )

    @staticmethod
    def _load(connection, run_id: str) -> OrganizationAssignmentSet:
        manifest = connection.execute(
            "SELECT * FROM landscape_v4_organization_manifests WHERE run_id=%s",
            (run_id,),
        ).fetchone()
        if manifest is None:
            raise KeyError(run_id)
        if manifest["policy_version"] != ORGANIZATION_POLICY_VERSION:
            raise OrganizationPersistenceError("unknown organization policy version")
        organization_rows = connection.execute(
            """
            SELECT * FROM landscape_v4_organizations
            WHERE run_id=%s ORDER BY sort_order
            """,
            (run_id,),
        ).fetchall()
        name_rows = connection.execute(
            """
            SELECT organization_id,observed_name,sort_order
            FROM landscape_v4_organization_names
            WHERE run_id=%s ORDER BY organization_id,sort_order
            """,
            (run_id,),
        ).fetchall()
        names: dict[str, list[str]] = {}
        for row in name_rows:
            values = names.setdefault(row["organization_id"], [])
            if row["sort_order"] != len(values) + 1:
                raise OrganizationPersistenceError("organization name order is not contiguous")
            values.append(row["observed_name"])
        assignment_rows = connection.execute(
            """
            SELECT * FROM landscape_v4_publication_organizations
            WHERE run_id=%s ORDER BY sort_order
            """,
            (run_id,),
        ).fetchall()
        co_rows = connection.execute(
            """
            SELECT publication_id,organization_id,sort_order
            FROM landscape_v4_publication_co_organizations
            WHERE run_id=%s ORDER BY publication_id,sort_order
            """,
            (run_id,),
        ).fetchall()
        applicant_rows = connection.execute(
            """
            SELECT publication_id,observed_name,is_unconfirmed,sort_order
            FROM landscape_v4_publication_applicants
            WHERE run_id=%s ORDER BY publication_id,sort_order
            """,
            (run_id,),
        ).fetchall()
        co_by_publication = _ordered_values(co_rows, "organization_id", "co-organization")
        applicants_by_publication = _ordered_values(
            applicant_rows, "observed_name", "applicant"
        )
        unconfirmed_by_publication: dict[str, list[str]] = {}
        for row in applicant_rows:
            if row["is_unconfirmed"]:
                unconfirmed_by_publication.setdefault(row["publication_id"], []).append(
                    row["observed_name"]
                )
        try:
            organizations = tuple(
                Organization(
                    organization_id=row["organization_id"],
                    display_name=row["display_name"],
                    normalized_name=row["normalized_name"],
                    organization_type=row["organization_type"],
                    source_profile_id=row["source_profile_id"],
                    observed_names=tuple(names.pop(row["organization_id"], ())),
                )
                for expected, row in enumerate(organization_rows, start=1)
                if _contiguous(row, expected, "organization")
            )
            assignments = tuple(
                PublicationOrganizationAssignment(
                    publication_id=row["publication_id"],
                    primary_organization_id=row["primary_organization_id"],
                    co_organization_ids=tuple(
                        co_by_publication.pop(row["publication_id"], ())
                    ),
                    observed_applicants=tuple(
                        applicants_by_publication.pop(row["publication_id"], ())
                    ),
                    unconfirmed_applicants=tuple(
                        unconfirmed_by_publication.pop(row["publication_id"], ())
                    ),
                )
                for expected, row in enumerate(assignment_rows, start=1)
                if _contiguous(row, expected, "assignment")
            )
            if names or co_by_publication or applicants_by_publication or unconfirmed_by_publication:
                raise OrganizationPersistenceError("organization child row has no parent")
            if manifest["organization_count"] != len(organizations):
                raise OrganizationPersistenceError("organization count mismatch")
            if manifest["assignment_count"] != len(assignments):
                raise OrganizationPersistenceError("assignment count mismatch")
            return OrganizationAssignmentSet(
                run_id=run_id,
                policy_version=manifest["policy_version"],
                organizations=organizations,
                assignments=assignments,
            )
        except (ValidationError, TypeError, ValueError) as exc:
            raise OrganizationPersistenceError(
                "stored organization assignments failed validation"
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


def _ordered_values(rows, value_key: str, label: str) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for row in rows:
        values = result.setdefault(row["publication_id"], [])
        if row["sort_order"] != len(values) + 1:
            raise OrganizationPersistenceError(f"{label} order is not contiguous")
        values.append(row[value_key])
    return result


def _contiguous(row, expected: int, label: str) -> bool:
    if row["sort_order"] != expected:
        raise OrganizationPersistenceError(f"{label} order is not contiguous")
    return True


__all__ = ["OrganizationPersistenceError", "PostgreSQLOrganizationRepository"]
