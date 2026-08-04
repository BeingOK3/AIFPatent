from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Iterator

from .abstract_evidence import AbstractEvidence


class AbstractPersistenceError(RuntimeError):
    pass


class PostgreSQLAbstractEvidenceRepository:
    def __init__(self, dsn: str, *, connect=None):
        if not isinstance(dsn, str) or not dsn.strip(): raise ValueError("PostgreSQL DSN must not be blank")
        self._dsn = dsn.strip(); self._connect_factory = connect

    def put(self, run_id: str, evidence: AbstractEvidence) -> AbstractEvidence:
        evidence = AbstractEvidence.model_validate(evidence.model_dump(mode="json"))
        with self._connect() as connection:
            if connection.execute("SELECT publication_id FROM landscape_v4_publications WHERE run_id=%s AND publication_id=%s", (run_id, evidence.publication_id)).fetchone() is None:
                raise KeyError((run_id, evidence.publication_id))
            inserted = connection.execute(
                """
                INSERT INTO landscape_v4_abstract_evidence(
                    run_id,publication_id,status,title,abstract_text,normalized_abstract,
                    content_hash,provider,unresolved_reason
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (run_id,publication_id) DO NOTHING RETURNING publication_id
                """,
                (run_id, evidence.publication_id, evidence.status.value, evidence.title,
                 evidence.abstract_text, evidence.normalized_abstract, evidence.content_hash,
                 evidence.provider, evidence.unresolved_reason),
            ).fetchone()
            if inserted is not None and evidence.evidence_ids:
                with connection.cursor() as cursor:
                    cursor.executemany(
                        "INSERT INTO landscape_v4_abstract_sentences(run_id,publication_id,evidence_id,sentence_order,sentence_text) VALUES (%s,%s,%s,%s,%s)",
                        [(run_id, evidence.publication_id, evidence_id, order, sentence)
                         for order, (evidence_id, sentence) in enumerate(zip(evidence.evidence_ids, _sentences(evidence.normalized_abstract), strict=True), start=1)],
                    )
            stored = self._load(connection, run_id, evidence.publication_id)
            if stored != evidence: raise AbstractPersistenceError("abstract evidence is immutable")
            return stored

    def get(self, run_id: str, publication_id: str) -> AbstractEvidence:
        with self._connect() as connection: return self._load(connection, run_id, publication_id)

    @staticmethod
    def _load(connection, run_id, publication_id):
        row = connection.execute("SELECT * FROM landscape_v4_abstract_evidence WHERE run_id=%s AND publication_id=%s", (run_id, publication_id)).fetchone()
        if row is None: raise KeyError((run_id, publication_id))
        sentences = connection.execute("SELECT evidence_id FROM landscape_v4_abstract_sentences WHERE run_id=%s AND publication_id=%s ORDER BY sentence_order", (run_id, publication_id)).fetchall()
        return AbstractEvidence(
            publication_id=row["publication_id"], status=row["status"], title=row["title"],
            abstract_text=row["abstract_text"], normalized_abstract=row["normalized_abstract"],
            content_hash=row["content_hash"], provider=row["provider"],
            evidence_ids=tuple(item["evidence_id"] for item in sentences), unresolved_reason=row["unresolved_reason"],
        )

    @contextmanager
    def _connect(self) -> Iterator[object]:
        if self._connect_factory is not None:
            with self._connect_factory() as connection: yield connection
            return
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(self._dsn, row_factory=dict_row, connect_timeout=10) as connection: yield connection


def _sentences(value: str) -> tuple[str, ...]:
    import re
    return tuple(sentence for sentence in re.split(r"(?<=[。！？!?\.])\s*", value) if sentence)


__all__ = ["AbstractPersistenceError", "PostgreSQLAbstractEvidenceRepository"]
