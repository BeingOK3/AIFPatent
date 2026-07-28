from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from typing import Any

from idea.postgres_database import PostgreSQLPersistenceError, _Connection
from idea.merge import normalize_publication_number

from .database import LandscapeDatabase, assert_no_secrets, canonical_json, now_ms


def _prepare_candidate_rows(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate and canonicalize the complete eligible candidate set."""

    prepared: list[dict[str, Any]] = []
    identities: set[tuple[str, str]] = set()
    ranks: set[int] = set()
    for candidate in candidates:
        metadata = candidate.get("metadata", {})
        assert_no_secrets(metadata)
        publication_number = str(candidate["publication_number"]).strip().upper()
        normalized = normalize_publication_number(publication_number)
        normalized_key = str(candidate["normalized_key"]).strip().upper()
        if not normalized or publication_number != normalized or normalized_key != normalized:
            raise ValueError(
                "candidate publication_number and normalized_key must be the canonical publication number"
            )
        document_id = str(candidate["document_id"]).strip()
        if not document_id:
            raise ValueError("candidate document_id cannot be blank")
        rank = candidate["rank"]
        if isinstance(rank, bool) or not isinstance(rank, int) or rank < 1:
            raise ValueError("candidate rank must be a positive integer")
        decision = str(candidate.get("decision", "ELIGIBLE")).strip().upper()
        if decision != "ELIGIBLE":
            raise ValueError("canonical candidates must have decision ELIGIBLE")
        if (document_id, normalized) in identities or rank in ranks:
            raise ValueError("candidate document, publication, normalized key and rank must be unique")
        if any(
            row["document_id"] == document_id
            or row["publication_number"] == normalized
            for row in prepared
        ):
            raise ValueError("candidate document, publication, normalized key and rank must be unique")
        identities.add((document_id, normalized))
        ranks.add(rank)
        payload = {
            "document_id": document_id,
            "publication_number": normalized,
            "normalized_key": normalized,
            "rank": rank,
            "decision": decision,
            "metadata": metadata,
        }
        encoded = canonical_json(payload)
        prepared.append(
            {
                **payload,
                "metadata_json": canonical_json(metadata),
                "content_hash": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
            }
        )
    if ranks and ranks != set(range(1, len(prepared) + 1)):
        raise ValueError("candidate ranks must be contiguous and start at 1")
    return sorted(prepared, key=lambda item: (item["rank"], item["publication_number"]))


def _candidate_comparison_value(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row["metadata_json"]
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    return {
        "document_id": row["document_id"],
        "publication_number": row["publication_number"],
        "normalized_key": row["normalized_key"],
        "rank": row["rank"],
        "decision": row["decision"],
        "metadata": metadata,
        "metadata_json": canonical_json(metadata),
        "content_hash": row["content_hash"],
    }


class LandscapePostgreSQLDatabase(LandscapeDatabase):
    """Landscape repository bound to the same PostgreSQL database as IDEA."""

    def __init__(self, dsn: str):
        if not dsn.strip():
            raise ValueError("PostgreSQL DSN must not be blank")
        self.dsn = dsn.strip()
        self.path = "<postgresql>"

    def initialize(self) -> None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT version FROM aifpatent_schema_migrations
                WHERE version = '070_landscape_company_analysis'
                """
            ).fetchone()
        if row is None:
            raise PostgreSQLPersistenceError(
                "PostgreSQL schema is not current; apply migration 070_landscape_company_analysis"
            )

    @contextmanager
    def connect(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover
            raise PostgreSQLPersistenceError("psycopg is required for PostgreSQL persistence") from exc
        raw = psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=10)
        connection = _Connection(raw)
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def table_names(self) -> set[str]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT table_name AS name
                FROM information_schema.tables
                WHERE table_schema = current_schema()
                """
            ).fetchall()
        return {row["name"] for row in rows}

    def put_candidates(
        self, run_id: str, candidates: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Persist the authoritative eligible set once, atomically and idempotently."""

        prepared = _prepare_candidate_rows(candidates)
        with self.connect() as connection:
            existing_rows = connection.execute(
                """
                SELECT document_id,publication_number,normalized_key,rank,decision,
                       metadata_json,content_hash
                FROM landscape_candidates
                WHERE run_id = %s
                ORDER BY rank,publication_number
                """,
                (run_id,),
            ).fetchall()
            if existing_rows:
                existing = [_candidate_comparison_value(dict(row)) for row in existing_rows]
                if canonical_json(existing) != canonical_json(prepared):
                    raise ValueError("landscape canonical candidate set is immutable")
            else:
                for candidate in prepared:
                    connection.execute(
                        """
                        INSERT INTO landscape_candidates(
                            run_id,document_id,publication_number,normalized_key,rank,
                            decision,metadata_json,content_hash,created_at
                        ) VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)
                        """,
                        (
                            run_id,
                            candidate["document_id"],
                            candidate["publication_number"],
                            candidate["normalized_key"],
                            candidate["rank"],
                            candidate["decision"],
                            candidate["metadata_json"],
                            candidate["content_hash"],
                            now_ms(),
                        ),
                    )
        return self.list_candidates(run_id)

    def list_candidates(self, run_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT document_id,publication_number,normalized_key,rank,decision,
                       metadata_json,content_hash,created_at
                FROM landscape_candidates
                WHERE run_id = %s
                ORDER BY rank,publication_number
                """,
                (run_id,),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for raw in rows:
            row = dict(raw)
            metadata = row.pop("metadata_json")
            row["metadata"] = json.loads(metadata) if isinstance(metadata, str) else metadata
            result.append(row)
        return result


__all__ = ["LandscapePostgreSQLDatabase"]
