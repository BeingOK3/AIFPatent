from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from typing import Iterator

from .classification_terminal import ClassificationResult
from .direction_record import DirectionRecord


class SemanticResultPersistenceError(RuntimeError):
    pass


class _PostgreSQLSemanticRepository:
    def __init__(self, dsn: str, *, connect=None):
        if not isinstance(dsn, str) or not dsn.strip():
            raise ValueError("PostgreSQL DSN must not be blank")
        self._dsn = dsn.strip()
        self._connect_factory = connect

    @staticmethod
    def _taxonomy_version(connection, run_id: str) -> str:
        row = connection.execute(
            "SELECT taxonomy_version FROM landscape_v4_runs WHERE run_id=%s",
            (run_id,),
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return row["taxonomy_version"]

    @contextmanager
    def _connect(self) -> Iterator[object]:
        if self._connect_factory is not None:
            with self._connect_factory() as connection:
                yield connection
            return
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(
            self._dsn,
            row_factory=dict_row,
            connect_timeout=10,
        ) as connection:
            yield connection


class PostgreSQLDirectionRepository(_PostgreSQLSemanticRepository):
    def put(self, run_id: str, record: DirectionRecord) -> DirectionRecord:
        record = DirectionRecord.model_validate(record.model_dump(mode="json"))
        input_hash = _hash(record.model_dump(mode="json"))
        with self._connect() as connection:
            taxonomy_version = self._taxonomy_version(connection, run_id)
            inserted = connection.execute(
                """
                INSERT INTO landscape_v4_direction_records(
                    run_id,analysis_unit_id,taxonomy_version,status,evidence_sufficient,
                    technical_problem,solution_mechanism,technical_object,direction_summary,
                    confidence,unresolved_reason,input_hash
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (run_id,analysis_unit_id) DO NOTHING
                RETURNING analysis_unit_id
                """,
                (
                    run_id,
                    record.analysis_unit_id,
                    taxonomy_version,
                    record.status.value,
                    record.evidence_sufficient,
                    record.technical_problem,
                    record.solution_mechanism,
                    record.technical_object,
                    record.direction_summary,
                    record.confidence,
                    record.unresolved_reason,
                    input_hash,
                ),
            ).fetchone()
            if inserted is not None:
                values = []
                for value_type, items in (
                    ("SCENARIO", record.application_scenarios),
                    ("KEYWORD", record.keywords),
                    ("LEVEL1", record.candidate_level1_ids),
                    ("EVIDENCE", record.evidence_ids),
                ):
                    values.extend(
                        (
                            run_id,
                            record.analysis_unit_id,
                            value_type,
                            value,
                            index,
                        )
                        for index, value in enumerate(items, start=1)
                    )
                if values:
                    with connection.cursor() as cursor:
                        cursor.executemany(
                            """
                            INSERT INTO landscape_v4_direction_values(
                                run_id,analysis_unit_id,value_type,value_text,sort_order
                            ) VALUES (%s,%s,%s,%s,%s)
                            """,
                            values,
                        )
            stored = self._load(connection, run_id, record.analysis_unit_id)
            if stored != record:
                raise SemanticResultPersistenceError("direction record is immutable")
            return stored

    def get(self, run_id: str, analysis_unit_id: str) -> DirectionRecord:
        with self._connect() as connection:
            return self._load(connection, run_id, analysis_unit_id)

    @staticmethod
    def _load(connection, run_id: str, analysis_unit_id: str) -> DirectionRecord:
        row = connection.execute(
            """
            SELECT * FROM landscape_v4_direction_records
            WHERE run_id=%s AND analysis_unit_id=%s
            """,
            (run_id, analysis_unit_id),
        ).fetchone()
        if row is None:
            raise KeyError((run_id, analysis_unit_id))
        values = connection.execute(
            """
            SELECT value_type,value_text FROM landscape_v4_direction_values
            WHERE run_id=%s AND analysis_unit_id=%s
            ORDER BY value_type,sort_order
            """,
            (run_id, analysis_unit_id),
        ).fetchall()
        grouped = {kind: [] for kind in ("SCENARIO", "KEYWORD", "LEVEL1", "EVIDENCE")}
        for value in values:
            grouped[value["value_type"]].append(value["value_text"])
        record = DirectionRecord(
            analysis_unit_id=row["analysis_unit_id"],
            status=row["status"],
            evidence_sufficient=row["evidence_sufficient"],
            technical_problem=row["technical_problem"],
            solution_mechanism=row["solution_mechanism"],
            technical_object=row["technical_object"],
            direction_summary=row["direction_summary"],
            application_scenarios=tuple(grouped["SCENARIO"]),
            keywords=tuple(grouped["KEYWORD"]),
            candidate_level1_ids=tuple(grouped["LEVEL1"]),
            evidence_ids=tuple(grouped["EVIDENCE"]),
            confidence=row["confidence"],
            unresolved_reason=row["unresolved_reason"],
        )
        if _hash(record.model_dump(mode="json")) != row["input_hash"]:
            raise SemanticResultPersistenceError("direction input hash mismatch")
        return record


class PostgreSQLClassificationRepository(_PostgreSQLSemanticRepository):
    def put(self, run_id: str, result: ClassificationResult) -> ClassificationResult:
        result = ClassificationResult.model_validate(result.model_dump(mode="json"))
        input_hash = _hash(result.model_dump(mode="json"))
        with self._connect() as connection:
            taxonomy_version = self._taxonomy_version(connection, run_id)
            inserted = connection.execute(
                """
                INSERT INTO landscape_v4_classification_results(
                    run_id,analysis_unit_id,taxonomy_version,action,terminal,
                    primary_category_id,confidence,review_round,unresolved_reason,input_hash
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (run_id,analysis_unit_id) DO NOTHING
                RETURNING analysis_unit_id
                """,
                (
                    run_id,
                    result.analysis_unit_id,
                    taxonomy_version,
                    result.action.value,
                    result.terminal.value,
                    result.primary_category_id,
                    result.confidence,
                    result.review_round,
                    result.unresolved_reason,
                    input_hash,
                ),
            ).fetchone()
            if inserted is not None:
                values = []
                for value_type, items in (
                    ("AUXILIARY_CATEGORY", result.auxiliary_category_ids),
                    ("EVIDENCE", result.evidence_ids),
                ):
                    values.extend(
                        (
                            run_id,
                            result.analysis_unit_id,
                            value_type,
                            value,
                            index,
                        )
                        for index, value in enumerate(items, start=1)
                    )
                if values:
                    with connection.cursor() as cursor:
                        cursor.executemany(
                            """
                            INSERT INTO landscape_v4_classification_values(
                                run_id,analysis_unit_id,value_type,value_text,sort_order
                            ) VALUES (%s,%s,%s,%s,%s)
                            """,
                            values,
                        )
            stored = self._load(connection, run_id, result.analysis_unit_id)
            if stored != result:
                raise SemanticResultPersistenceError("classification result is immutable")
            return stored

    def get(self, run_id: str, analysis_unit_id: str) -> ClassificationResult:
        with self._connect() as connection:
            return self._load(connection, run_id, analysis_unit_id)

    @staticmethod
    def _load(connection, run_id: str, analysis_unit_id: str) -> ClassificationResult:
        row = connection.execute(
            """
            SELECT * FROM landscape_v4_classification_results
            WHERE run_id=%s AND analysis_unit_id=%s
            """,
            (run_id, analysis_unit_id),
        ).fetchone()
        if row is None:
            raise KeyError((run_id, analysis_unit_id))
        values = connection.execute(
            """
            SELECT value_type,value_text FROM landscape_v4_classification_values
            WHERE run_id=%s AND analysis_unit_id=%s
            ORDER BY value_type,sort_order
            """,
            (run_id, analysis_unit_id),
        ).fetchall()
        grouped = {kind: [] for kind in ("AUXILIARY_CATEGORY", "EVIDENCE")}
        for value in values:
            grouped[value["value_type"]].append(value["value_text"])
        result = ClassificationResult(
            analysis_unit_id=row["analysis_unit_id"],
            action=row["action"],
            terminal=row["terminal"],
            primary_category_id=row["primary_category_id"],
            auxiliary_category_ids=tuple(grouped["AUXILIARY_CATEGORY"]),
            evidence_ids=tuple(grouped["EVIDENCE"]),
            confidence=row["confidence"],
            review_round=row["review_round"],
            unresolved_reason=row["unresolved_reason"],
        )
        if _hash(result.model_dump(mode="json")) != row["input_hash"]:
            raise SemanticResultPersistenceError("classification input hash mismatch")
        return result


def _hash(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


__all__ = [
    "PostgreSQLClassificationRepository",
    "PostgreSQLDirectionRepository",
    "SemanticResultPersistenceError",
]
