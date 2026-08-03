from __future__ import annotations

import hashlib
import json
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .taxonomy import (
    TaxonomyArtifact,
    TaxonomyCompileError,
    compile_taxonomy_file,
)


class TaxonomyPersistenceError(RuntimeError):
    pass


class PostgreSQLTaxonomyRepository:
    """PostgreSQL-native immutable repository for compiled taxonomies."""

    def __init__(self, dsn: str):
        if not isinstance(dsn, str) or not dsn.strip():
            raise ValueError("PostgreSQL DSN must not be blank")
        self._dsn = dsn.strip()

    def ensure_current(self, source_path: str | Path) -> TaxonomyArtifact:
        return self.put(compile_taxonomy_file(source_path))

    def put(self, artifact: TaxonomyArtifact) -> TaxonomyArtifact:
        # Re-parse our own serialization before persistence. This prevents a
        # manually constructed dataclass from bypassing compiler invariants.
        artifact = TaxonomyArtifact.from_dict(artifact.to_dict())
        version_row, category_rows = prepare_taxonomy_rows(artifact)

        try:
            from psycopg.types.json import Jsonb
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise TaxonomyPersistenceError("psycopg is required for taxonomy persistence") from exc

        with self._connect() as connection:
            inserted = connection.execute(
                """
                INSERT INTO landscape_taxonomy_versions(
                    taxonomy_version,schema_version,taxonomy_hash,source_hash,
                    source_row_count,node_count,leaf_count,artifact_json,created_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (taxonomy_version) DO NOTHING
                RETURNING taxonomy_version
                """,
                (
                    version_row["taxonomy_version"],
                    version_row["schema_version"],
                    version_row["taxonomy_hash"],
                    version_row["source_hash"],
                    version_row["source_row_count"],
                    version_row["node_count"],
                    version_row["leaf_count"],
                    Jsonb(version_row["artifact_json"]),
                    version_row["created_at"],
                ),
            ).fetchone()
            if inserted is not None:
                with connection.cursor() as cursor:
                    cursor.executemany(
                        """
                        INSERT INTO landscape_taxonomy_categories(
                            taxonomy_version,category_id,parent_id,level,name,
                            path_json,is_leaf,sort_order,content_hash,created_at
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        """,
                        [
                            (
                                row["taxonomy_version"],
                                row["category_id"],
                                row["parent_id"],
                                row["level"],
                                row["name"],
                                Jsonb(row["path_json"]),
                                row["is_leaf"],
                                row["sort_order"],
                                row["content_hash"],
                                row["created_at"],
                            )
                            for row in category_rows
                        ],
                    )

        stored = self.get(artifact.taxonomy_version)
        if stored.taxonomy_hash != artifact.taxonomy_hash:
            raise TaxonomyPersistenceError(
                "stored taxonomy version conflicts with compiled taxonomy hash"
            )
        return stored

    def get(self, taxonomy_version: str) -> TaxonomyArtifact:
        with self._connect() as connection:
            version_row = connection.execute(
                """
                SELECT taxonomy_version,schema_version,taxonomy_hash,source_hash,
                       source_row_count,node_count,leaf_count,artifact_json
                FROM landscape_taxonomy_versions
                WHERE taxonomy_version=%s
                """,
                (taxonomy_version,),
            ).fetchone()
            if version_row is None:
                raise KeyError(taxonomy_version)
            category_rows = connection.execute(
                """
                SELECT category_id,parent_id,level,name,path_json,is_leaf,
                       sort_order,content_hash
                FROM landscape_taxonomy_categories
                WHERE taxonomy_version=%s
                ORDER BY sort_order
                """,
                (taxonomy_version,),
            ).fetchall()
        return validate_persisted_taxonomy(version_row, category_rows)

    @contextmanager
    def _connect(self) -> Iterator[object]:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise TaxonomyPersistenceError("psycopg is required for taxonomy persistence") from exc
        with psycopg.connect(self._dsn, row_factory=dict_row, connect_timeout=10) as connection:
            yield connection


def prepare_taxonomy_rows(
    artifact: TaxonomyArtifact,
    *,
    created_at: int | None = None,
) -> tuple[dict, list[dict]]:
    artifact = TaxonomyArtifact.from_dict(artifact.to_dict())
    timestamp = int(time.time() * 1000) if created_at is None else created_at
    if not isinstance(timestamp, int) or isinstance(timestamp, bool) or timestamp < 0:
        raise ValueError("created_at must be a non-negative integer")
    node_payloads = artifact.to_dict()["nodes"]
    categories = []
    for node in node_payloads:
        categories.append(
            {
            "taxonomy_version": artifact.taxonomy_version,
            "category_id": node["category_id"],
            "parent_id": node["parent_id"],
            "level": node["level"],
            "name": node["name"],
            "path_json": node["path"],
            "is_leaf": node["is_leaf"],
            "sort_order": node["sort_order"],
            "content_hash": _content_hash(node),
            "created_at": timestamp,
            }
        )
    version = {
        "taxonomy_version": artifact.taxonomy_version,
        "schema_version": artifact.schema_version,
        "taxonomy_hash": artifact.taxonomy_hash,
        "source_hash": artifact.source_hash,
        "source_row_count": artifact.source_row_count,
        "node_count": len(artifact.nodes),
        "leaf_count": len(artifact.leaf_category_ids),
        "artifact_json": artifact.to_dict(),
        "created_at": timestamp,
    }
    return version, categories


def validate_persisted_taxonomy(
    version_row: dict,
    category_rows: list[dict],
) -> TaxonomyArtifact:
    raw_artifact = version_row.get("artifact_json")
    if isinstance(raw_artifact, str):
        try:
            raw_artifact = json.loads(raw_artifact)
        except json.JSONDecodeError as exc:
            raise TaxonomyPersistenceError("stored taxonomy artifact is invalid JSON") from exc
    try:
        artifact = TaxonomyArtifact.from_dict(raw_artifact)
    except TaxonomyCompileError as exc:
        raise TaxonomyPersistenceError("stored taxonomy artifact failed validation") from exc

    expected_version = {
        "taxonomy_version": artifact.taxonomy_version,
        "schema_version": artifact.schema_version,
        "taxonomy_hash": artifact.taxonomy_hash,
        "source_hash": artifact.source_hash,
        "source_row_count": artifact.source_row_count,
        "node_count": len(artifact.nodes),
        "leaf_count": len(artifact.leaf_category_ids),
    }
    for key, expected in expected_version.items():
        if version_row.get(key) != expected:
            raise TaxonomyPersistenceError(f"stored taxonomy version field mismatch: {key}")

    expected_rows = prepare_taxonomy_rows(artifact, created_at=0)[1]
    if len(category_rows) != len(expected_rows):
        raise TaxonomyPersistenceError("stored taxonomy category count mismatch")
    for actual, expected in zip(category_rows, expected_rows, strict=True):
        normalized_actual = dict(actual)
        path_json = normalized_actual.get("path_json")
        if isinstance(path_json, str):
            try:
                normalized_actual["path_json"] = json.loads(path_json)
            except json.JSONDecodeError as exc:
                raise TaxonomyPersistenceError(
                    "stored taxonomy category path is invalid JSON"
                ) from exc
        expected_without_storage = {
            key: value
            for key, value in expected.items()
            if key not in {"taxonomy_version", "created_at"}
        }
        if normalized_actual != expected_without_storage:
            raise TaxonomyPersistenceError(
                f"stored taxonomy category mismatch: {expected['category_id']}"
            )
    return artifact


def _content_hash(payload: dict) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = [
    "PostgreSQLTaxonomyRepository",
    "TaxonomyPersistenceError",
    "prepare_taxonomy_rows",
    "validate_persisted_taxonomy",
]
