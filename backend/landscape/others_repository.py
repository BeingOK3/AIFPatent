from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from typing import Iterator

from .others_discovery import OthersCluster, OthersDiscovery
from .semantic_result_repository import SemanticResultPersistenceError


class PostgreSQLOthersRepository:
    """Append-only persistence for the per-Run Others partition."""

    def __init__(self, dsn: str, *, connect=None):
        if not isinstance(dsn, str) or not dsn.strip():
            raise ValueError("PostgreSQL DSN must not be blank")
        self._dsn = dsn.strip()
        self._connect_factory = connect

    def put(self, run_id: str, discovery: OthersDiscovery) -> OthersDiscovery:
        discovery = OthersDiscovery.model_validate(discovery.model_dump(mode="json"))
        discovery_hash = _hash(discovery.model_dump(mode="json"))
        with self._connect() as connection:
            taxonomy_version = self._taxonomy_version(connection, run_id)
            connection.execute(
                """
                INSERT INTO landscape_v4_others_manifests(
                    run_id,taxonomy_version,algorithm_version,member_count,cluster_count,input_hash
                ) VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (run_id) DO NOTHING
                """,
                (
                    run_id,
                    taxonomy_version,
                    discovery.algorithm_version,
                    len(discovery.source_member_ids),
                    len(discovery.clusters),
                    discovery_hash,
                ),
            )
            manifest = self._manifest(connection, run_id)
            if (
                manifest["taxonomy_version"] != taxonomy_version
                or manifest["algorithm_version"] != discovery.algorithm_version
                or manifest["member_count"] != len(discovery.source_member_ids)
                or manifest["cluster_count"] != len(discovery.clusters)
                or manifest["input_hash"] != discovery_hash
            ):
                raise SemanticResultPersistenceError("Others partition is immutable")
            for cluster in discovery.clusters:
                self._put_cluster(connection, run_id, taxonomy_version, cluster)
            loaded = self._load(connection, run_id, taxonomy_version)
            if loaded != discovery:
                raise SemanticResultPersistenceError("Others partition is immutable")
            return loaded

    def get(self, run_id: str) -> OthersDiscovery:
        with self._connect() as connection:
            taxonomy_version = self._taxonomy_version(connection, run_id)
            return self._load(connection, run_id, taxonomy_version)

    @staticmethod
    def _taxonomy_version(connection, run_id: str) -> str:
        row = connection.execute(
            "SELECT taxonomy_version FROM landscape_v4_runs WHERE run_id=%s",
            (run_id,),
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return row["taxonomy_version"]

    @staticmethod
    def _manifest(connection, run_id: str):
        row = connection.execute(
            "SELECT * FROM landscape_v4_others_manifests WHERE run_id=%s",
            (run_id,),
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return row

    @staticmethod
    def _put_cluster(connection, run_id: str, taxonomy_version: str, cluster: OthersCluster) -> None:
        input_hash = _hash(cluster.model_dump(mode="json"))
        inserted = connection.execute(
            """
            INSERT INTO landscape_v4_others_clusters(
                run_id,cluster_id,taxonomy_version,algorithm_version,kind,
                representative_analysis_unit_id,cohesion,name,technical_problem,
                common_mechanism,direction_boundary,keywords_json,naming_source,input_hash
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (run_id,cluster_id) DO NOTHING RETURNING cluster_id
            """,
            (
                run_id,
                cluster.cluster_id,
                taxonomy_version,
                cluster.algorithm_version,
                cluster.kind.value,
                cluster.representative_analysis_unit_id,
                cluster.cohesion,
                cluster.name,
                cluster.technical_problem,
                cluster.common_mechanism,
                cluster.direction_boundary,
                json.dumps(cluster.keywords, ensure_ascii=False),
                cluster.naming_source,
                input_hash,
            ),
        ).fetchone()
        if inserted is not None:
            with connection.cursor() as cursor:
                cursor.executemany(
                    """
                    INSERT INTO landscape_v4_others_members(
                        run_id,cluster_id,analysis_unit_id,sort_order
                    ) VALUES (%s,%s,%s,%s)
                    """,
                    [
                        (run_id, cluster.cluster_id, member_id, index)
                        for index, member_id in enumerate(cluster.member_ids, start=1)
                    ],
                )

    @staticmethod
    def _load(connection, run_id: str, taxonomy_version: str) -> OthersDiscovery:
        manifest = PostgreSQLOthersRepository._manifest(connection, run_id)
        rows = connection.execute(
            """
            SELECT * FROM landscape_v4_others_clusters
            WHERE run_id=%s AND taxonomy_version=%s ORDER BY cluster_id
            """,
            (run_id, taxonomy_version),
        ).fetchall()
        clusters = []
        all_members = []
        for row in rows:
            members = connection.execute(
                """
                SELECT analysis_unit_id FROM landscape_v4_others_members
                WHERE run_id=%s AND cluster_id=%s ORDER BY sort_order
                """,
                (run_id, row["cluster_id"]),
            ).fetchall()
            member_ids = tuple(member["analysis_unit_id"] for member in members)
            keywords = row["keywords_json"]
            if isinstance(keywords, str):
                keywords = json.loads(keywords)
            cluster = OthersCluster(
                cluster_id=row["cluster_id"],
                algorithm_version=row["algorithm_version"],
                kind=row["kind"],
                member_ids=member_ids,
                representative_analysis_unit_id=row["representative_analysis_unit_id"],
                cohesion=row["cohesion"],
                name=row["name"],
                technical_problem=row["technical_problem"],
                common_mechanism=row["common_mechanism"],
                direction_boundary=row["direction_boundary"],
                keywords=tuple(keywords),
                naming_source=row["naming_source"],
            )
            if _hash(cluster.model_dump(mode="json")) != row["input_hash"]:
                raise SemanticResultPersistenceError("Others input hash mismatch")
            clusters.append(cluster)
            all_members.extend(member_ids)
        source_members = tuple(sorted(all_members))
        discovery = OthersDiscovery(
            algorithm_version=manifest["algorithm_version"],
            source_member_ids=source_members,
            clusters=tuple(clusters),
        )
        if len(source_members) != manifest["member_count"] or len(clusters) != manifest["cluster_count"]:
            raise SemanticResultPersistenceError("Others manifest count mismatch")
        if _hash(discovery.model_dump(mode="json")) != manifest["input_hash"]:
            raise SemanticResultPersistenceError("Others manifest hash mismatch")
        return discovery

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


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


__all__ = ["PostgreSQLOthersRepository"]
