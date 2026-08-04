from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from typing import Iterator

from .metrics import MetricCell, MetricCube, TimeBucket
from .representatives import RepresentativePatent
from .semantic_result_repository import SemanticResultPersistenceError
from .trends import TREND_POLICY_VERSION, TrendBucketMetric, TrendCandidate


class _PostgreSQLAnalyticsRepository:
    def __init__(self, dsn: str, *, connect=None):
        if not isinstance(dsn, str) or not dsn.strip():
            raise ValueError("PostgreSQL DSN must not be blank")
        self._dsn = dsn.strip()
        self._connect_factory = connect

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


class PostgreSQLMetricRepository(_PostgreSQLAnalyticsRepository):
    def put(self, run_id: str, cube: MetricCube) -> MetricCube:
        cube = MetricCube.model_validate(cube.model_dump(mode="json"))
        with self._connect() as connection:
            inserted = connection.execute(
                """
                INSERT INTO landscape_v4_metric_manifests(
                    run_id,policy_version,publication_start,publication_end,
                    organization_counting_mode,analysis_unit_time_policy,
                    analysis_unit_count,publication_count,excluded_publication_count,cube_hash
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (run_id) DO NOTHING RETURNING run_id
                """,
                (
                    run_id,
                    cube.policy_version,
                    cube.publication_start,
                    cube.publication_end,
                    cube.organization_counting_mode.value,
                    cube.analysis_unit_time_policy,
                    cube.analysis_unit_count,
                    cube.publication_count,
                    cube.excluded_out_of_range_publication_count,
                    cube.cube_hash,
                ),
            ).fetchone()
            if inserted is not None:
                self._insert_cube_rows(connection, run_id, cube)
            stored = self._load(connection, run_id)
            if stored != cube:
                raise SemanticResultPersistenceError("metric cube is immutable")
            return stored

    def get(self, run_id: str) -> MetricCube:
        with self._connect() as connection:
            return self._load(connection, run_id)

    @staticmethod
    def _insert_cube_rows(connection, run_id: str, cube: MetricCube) -> None:
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO landscape_v4_metric_buckets(
                    run_id,bucket_id,sort_order,label,bucket_start,bucket_end,granularity
                ) VALUES (%s,%s,%s,%s,%s,%s,%s)
                """,
                [
                    (
                        run_id,
                        bucket.bucket_id,
                        index,
                        bucket.label,
                        bucket.start,
                        bucket.end,
                        bucket.granularity.value,
                    )
                    for index, bucket in enumerate(cube.buckets, start=1)
                ],
            )
            cursor.executemany(
                """
                INSERT INTO landscape_v4_metric_cells(
                    run_id,direction_id,organization_id,bucket_id,
                    analysis_unit_count,publication_count,direction_share
                ) VALUES (%s,%s,%s,%s,%s,%s,%s)
                """,
                [
                    (
                        run_id,
                        cell.direction_id,
                        cell.organization_id,
                        cell.bucket_id,
                        cell.analysis_unit_count,
                        cell.publication_count,
                        cell.direction_share,
                    )
                    for cell in cube.cells
                ],
            )
            values = []
            for cell in cube.cells:
                for value_type, identifiers in (
                    ("ANALYSIS_UNIT", cell.analysis_unit_ids),
                    ("PUBLICATION", cell.publication_ids),
                ):
                    values.extend(
                        (
                            run_id,
                            cell.direction_id,
                            cell.organization_id,
                            cell.bucket_id,
                            value_type,
                            identifier,
                            index,
                        )
                        for index, identifier in enumerate(identifiers, start=1)
                    )
            if values:
                cursor.executemany(
                    """
                    INSERT INTO landscape_v4_metric_cell_values(
                        run_id,direction_id,organization_id,bucket_id,
                        value_type,value_id,sort_order
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s)
                    """,
                    values,
                )

    @staticmethod
    def _load(connection, run_id: str) -> MetricCube:
        manifest = connection.execute(
            "SELECT * FROM landscape_v4_metric_manifests WHERE run_id=%s",
            (run_id,),
        ).fetchone()
        if manifest is None:
            raise KeyError(run_id)
        bucket_rows = connection.execute(
            """
            SELECT * FROM landscape_v4_metric_buckets
            WHERE run_id=%s ORDER BY sort_order
            """,
            (run_id,),
        ).fetchall()
        buckets = tuple(
            TimeBucket(
                bucket_id=row["bucket_id"],
                label=row["label"],
                start=row["bucket_start"],
                end=row["bucket_end"],
                granularity=row["granularity"],
            )
            for row in bucket_rows
        )
        cell_rows = connection.execute(
            """
            SELECT * FROM landscape_v4_metric_cells
            WHERE run_id=%s ORDER BY direction_id,organization_id,bucket_id
            """,
            (run_id,),
        ).fetchall()
        cells = []
        for row in cell_rows:
            values = connection.execute(
                """
                SELECT value_type,value_id FROM landscape_v4_metric_cell_values
                WHERE run_id=%s AND direction_id=%s AND organization_id=%s AND bucket_id=%s
                ORDER BY value_type,sort_order
                """,
                (
                    run_id,
                    row["direction_id"],
                    row["organization_id"],
                    row["bucket_id"],
                ),
            ).fetchall()
            grouped = {"ANALYSIS_UNIT": [], "PUBLICATION": []}
            for value in values:
                grouped[value["value_type"]].append(value["value_id"])
            cells.append(
                MetricCell(
                    direction_id=row["direction_id"],
                    organization_id=row["organization_id"],
                    bucket_id=row["bucket_id"],
                    analysis_unit_ids=tuple(grouped["ANALYSIS_UNIT"]),
                    publication_ids=tuple(grouped["PUBLICATION"]),
                    analysis_unit_count=row["analysis_unit_count"],
                    publication_count=row["publication_count"],
                    direction_share=row["direction_share"],
                )
            )
        return MetricCube(
            policy_version=manifest["policy_version"],
            publication_start=manifest["publication_start"],
            publication_end=manifest["publication_end"],
            organization_counting_mode=manifest["organization_counting_mode"],
            analysis_unit_time_policy=manifest["analysis_unit_time_policy"],
            buckets=buckets,
            cells=tuple(cells),
            analysis_unit_count=manifest["analysis_unit_count"],
            publication_count=manifest["publication_count"],
            excluded_out_of_range_publication_count=manifest[
                "excluded_publication_count"
            ],
            cube_hash=manifest["cube_hash"],
        )


class PostgreSQLTrendRepository(_PostgreSQLAnalyticsRepository):
    def put(
        self,
        run_id: str,
        candidates: tuple[TrendCandidate, ...],
        *,
        policy_version: str = TREND_POLICY_VERSION,
    ) -> tuple[TrendCandidate, ...]:
        candidates = tuple(
            TrendCandidate.model_validate(candidate.model_dump(mode="json"))
            for candidate in candidates
        )
        if tuple(sorted(candidates, key=lambda item: item.candidate_id)) != candidates:
            raise ValueError("trend candidates must use stable candidate ID order")
        if len({item.candidate_id for item in candidates}) != len(candidates):
            raise ValueError("duplicate trend candidate")
        if any(item.policy_version != policy_version for item in candidates):
            raise ValueError("trend candidate policy version mismatch")
        input_hash = _hash(
            {
                "policy_version": policy_version,
                "candidates": [item.model_dump(mode="json") for item in candidates],
            }
        )
        with self._connect() as connection:
            inserted = connection.execute(
                """
                INSERT INTO landscape_v4_trend_manifests(
                    run_id,policy_version,candidate_count,input_hash
                ) VALUES (%s,%s,%s,%s)
                ON CONFLICT (run_id) DO NOTHING RETURNING run_id
                """,
                (run_id, policy_version, len(candidates), input_hash),
            ).fetchone()
            if inserted is not None:
                self._insert_candidates(connection, run_id, candidates)
            stored = self._load(connection, run_id)
            if stored != candidates:
                raise SemanticResultPersistenceError("trend candidates are immutable")
            return stored

    def get(self, run_id: str) -> tuple[TrendCandidate, ...]:
        with self._connect() as connection:
            return self._load(connection, run_id)

    @staticmethod
    def _insert_candidates(connection, run_id: str, candidates: tuple[TrendCandidate, ...]) -> None:
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO landscape_v4_trend_candidates(
                    run_id,candidate_id,direction_id,change_type,conclusion_strength
                ) VALUES (%s,%s,%s,%s,%s)
                """,
                [
                    (
                        run_id,
                        candidate.candidate_id,
                        candidate.direction_id,
                        candidate.change_type.value,
                        candidate.allowed_conclusion_strength.value,
                    )
                    for candidate in candidates
                ],
            )
            bucket_rows = []
            value_rows = []
            for candidate in candidates:
                bucket_rows.extend(
                    (
                        run_id,
                        candidate.candidate_id,
                        metric.bucket_id,
                        index,
                        metric.analysis_unit_count,
                        metric.publication_count,
                    )
                    for index, metric in enumerate(candidate.bucket_metrics, start=1)
                )
                for value_type, identifiers in (
                    ("ORGANIZATION", candidate.organization_ids),
                    ("ANALYSIS_UNIT", candidate.analysis_unit_ids),
                    ("REPRESENTATIVE", candidate.representative_analysis_unit_ids),
                    ("EVIDENCE", candidate.evidence_ids),
                    ("LIMITATION", candidate.limitation_codes),
                ):
                    value_rows.extend(
                        (
                            run_id,
                            candidate.candidate_id,
                            value_type,
                            identifier,
                            index,
                        )
                        for index, identifier in enumerate(identifiers, start=1)
                    )
            if bucket_rows:
                cursor.executemany(
                    """
                    INSERT INTO landscape_v4_trend_bucket_metrics(
                        run_id,candidate_id,bucket_id,sort_order,
                        analysis_unit_count,publication_count
                    ) VALUES (%s,%s,%s,%s,%s,%s)
                    """,
                    bucket_rows,
                )
            if value_rows:
                cursor.executemany(
                    """
                    INSERT INTO landscape_v4_trend_values(
                        run_id,candidate_id,value_type,value_id,sort_order
                    ) VALUES (%s,%s,%s,%s,%s)
                    """,
                    value_rows,
                )

    @staticmethod
    def _load(connection, run_id: str) -> tuple[TrendCandidate, ...]:
        manifest = connection.execute(
            "SELECT * FROM landscape_v4_trend_manifests WHERE run_id=%s",
            (run_id,),
        ).fetchone()
        if manifest is None:
            raise KeyError(run_id)
        rows = connection.execute(
            """
            SELECT * FROM landscape_v4_trend_candidates
            WHERE run_id=%s ORDER BY candidate_id
            """,
            (run_id,),
        ).fetchall()
        candidates = []
        for row in rows:
            bucket_rows = connection.execute(
                """
                SELECT * FROM landscape_v4_trend_bucket_metrics
                WHERE run_id=%s AND candidate_id=%s ORDER BY sort_order
                """,
                (run_id, row["candidate_id"]),
            ).fetchall()
            value_rows = connection.execute(
                """
                SELECT value_type,value_id FROM landscape_v4_trend_values
                WHERE run_id=%s AND candidate_id=%s ORDER BY value_type,sort_order
                """,
                (run_id, row["candidate_id"]),
            ).fetchall()
            grouped = {
                kind: []
                for kind in (
                    "ORGANIZATION",
                    "ANALYSIS_UNIT",
                    "REPRESENTATIVE",
                    "EVIDENCE",
                    "LIMITATION",
                )
            }
            for value in value_rows:
                grouped[value["value_type"]].append(value["value_id"])
            candidates.append(
                TrendCandidate(
                    candidate_id=row["candidate_id"],
                    policy_version=manifest["policy_version"],
                    direction_id=row["direction_id"],
                    organization_ids=tuple(grouped["ORGANIZATION"]),
                    bucket_metrics=tuple(
                        TrendBucketMetric(
                            bucket_id=bucket["bucket_id"],
                            analysis_unit_count=bucket["analysis_unit_count"],
                            publication_count=bucket["publication_count"],
                        )
                        for bucket in bucket_rows
                    ),
                    change_type=row["change_type"],
                    allowed_conclusion_strength=row["conclusion_strength"],
                    analysis_unit_ids=tuple(grouped["ANALYSIS_UNIT"]),
                    representative_analysis_unit_ids=tuple(grouped["REPRESENTATIVE"]),
                    evidence_ids=tuple(grouped["EVIDENCE"]),
                    limitation_codes=tuple(grouped["LIMITATION"]),
                )
            )
        stored = tuple(candidates)
        expected_hash = _hash(
            {
                "policy_version": manifest["policy_version"],
                "candidates": [item.model_dump(mode="json") for item in stored],
            }
        )
        if len(stored) != manifest["candidate_count"] or expected_hash != manifest["input_hash"]:
            raise SemanticResultPersistenceError("trend manifest mismatch")
        return stored


class PostgreSQLRepresentativeRepository(_PostgreSQLAnalyticsRepository):
    def put(
        self,
        run_id: str,
        representatives: tuple[RepresentativePatent, ...],
    ) -> tuple[RepresentativePatent, ...]:
        representatives = tuple(
            RepresentativePatent.model_validate(item.model_dump(mode="json"))
            for item in representatives
        )
        _validate_representatives(representatives)
        input_hash = _hash([item.model_dump(mode="json") for item in representatives])
        with self._connect() as connection:
            inserted = connection.execute(
                """
                INSERT INTO landscape_v4_representative_manifests(
                    run_id,representative_count,input_hash
                ) VALUES (%s,%s,%s)
                ON CONFLICT (run_id) DO NOTHING RETURNING run_id
                """,
                (run_id, len(representatives), input_hash),
            ).fetchone()
            if inserted is not None and representatives:
                with connection.cursor() as cursor:
                    cursor.executemany(
                        """
                        INSERT INTO landscape_v4_representatives(
                            run_id,representative_id,selection_rank,direction_id,
                            analysis_unit_id,publication_id,title,publication_number,
                            publication_date,organization_ids_json,classification_path_json,
                            selection_reasons_json,patent_url,link_status
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        """,
                        [
                            (
                                run_id,
                                item.representative_id,
                                item.selection_rank,
                                item.direction_id,
                                item.analysis_unit_id,
                                item.publication_id,
                                item.title,
                                item.publication_number,
                                item.publication_date,
                                json.dumps(item.organization_ids, ensure_ascii=False),
                                json.dumps(item.classification_path, ensure_ascii=False),
                                json.dumps(item.selection_reasons, ensure_ascii=False),
                                item.patent_url,
                                item.link_status.value,
                            )
                            for item in representatives
                        ],
                    )
            stored = self._load(connection, run_id)
            if stored != representatives:
                raise SemanticResultPersistenceError("representatives are immutable")
            return stored

    def get(self, run_id: str) -> tuple[RepresentativePatent, ...]:
        with self._connect() as connection:
            return self._load(connection, run_id)

    @staticmethod
    def _load(connection, run_id: str) -> tuple[RepresentativePatent, ...]:
        manifest = connection.execute(
            "SELECT * FROM landscape_v4_representative_manifests WHERE run_id=%s",
            (run_id,),
        ).fetchone()
        if manifest is None:
            raise KeyError(run_id)
        rows = connection.execute(
            """
            SELECT * FROM landscape_v4_representatives
            WHERE run_id=%s ORDER BY direction_id,selection_rank
            """,
            (run_id,),
        ).fetchall()
        representatives = tuple(
            RepresentativePatent(
                representative_id=row["representative_id"],
                selection_rank=row["selection_rank"],
                direction_id=row["direction_id"],
                analysis_unit_id=row["analysis_unit_id"],
                publication_id=row["publication_id"],
                title=row["title"],
                publication_number=row["publication_number"],
                organization_ids=tuple(_json_value(row["organization_ids_json"])),
                publication_date=row["publication_date"],
                classification_path=tuple(_json_value(row["classification_path_json"])),
                selection_reasons=tuple(_json_value(row["selection_reasons_json"])),
                patent_url=row["patent_url"],
                link_status=row["link_status"],
            )
            for row in rows
        )
        _validate_representatives(representatives)
        if (
            len(representatives) != manifest["representative_count"]
            or _hash([item.model_dump(mode="json") for item in representatives])
            != manifest["input_hash"]
        ):
            raise SemanticResultPersistenceError("representative manifest mismatch")
        return representatives


def _validate_representatives(representatives: tuple[RepresentativePatent, ...]) -> None:
    expected = tuple(
        sorted(
            representatives,
            key=lambda item: (item.direction_id, item.selection_rank),
        )
    )
    if expected != representatives:
        raise ValueError("representatives must use direction and selection rank order")
    identities = [item.representative_id for item in representatives]
    memberships = [(item.direction_id, item.analysis_unit_id) for item in representatives]
    ranks = [(item.direction_id, item.selection_rank) for item in representatives]
    if len(identities) != len(set(identities)) or len(memberships) != len(set(memberships)):
        raise ValueError("duplicate representative membership")
    if len(ranks) != len(set(ranks)):
        raise ValueError("duplicate representative selection rank")


def _json_value(value):
    return json.loads(value) if isinstance(value, str) else value


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


__all__ = [
    "PostgreSQLMetricRepository",
    "PostgreSQLRepresentativeRepository",
    "PostgreSQLTrendRepository",
]
