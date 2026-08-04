from __future__ import annotations

import unittest
from contextlib import contextmanager

from landscape.abstract_evidence import abstract_from_document
from landscape.abstract_repository import PostgreSQLAbstractEvidenceRepository, AbstractPersistenceError
from tests.test_landscape_abstract_evidence import document


class Cursor:
    def __init__(self, connection, row=None, rows=None): self.connection=connection; self.row=row; self.rows=rows or ([] if row is None else [row])
    def fetchone(self): return self.row
    def fetchall(self): return self.rows
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def executemany(self, _sql, values):
        self.connection.sentences.extend({"evidence_id": row[2], "sentence_order": row[3]} for row in values)


class Connection:
    def __init__(self): self.publication=True; self.row=None; self.sentences=[]
    def cursor(self): return Cursor(self)
    def execute(self, sql, params=()):
        normalized=" ".join(sql.split())
        if "FROM landscape_v4_publications" in normalized: return Cursor(self, {"publication_id": params[1]} if self.publication else None)
        if normalized.startswith("INSERT INTO landscape_v4_abstract_evidence"):
            if self.row is not None: return Cursor(self)
            keys=("run_id","publication_id","status","title","abstract_text","normalized_abstract","content_hash","provider","unresolved_reason")
            self.row=dict(zip(keys, params, strict=True)); return Cursor(self, {"publication_id": params[1]})
        if "FROM landscape_v4_abstract_evidence" in normalized: return Cursor(self, self.row)
        if "FROM landscape_v4_abstract_sentences" in normalized: return Cursor(self, rows=self.sentences)
        raise AssertionError(normalized)


class LandscapeAbstractRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.connection=Connection()
        @contextmanager
        def connect(): yield self.connection
        self.repository=PostgreSQLAbstractEvidenceRepository("postgresql://fixture", connect=connect)

    def test_available_abstract_is_replayable_and_idempotent(self):
        evidence=abstract_from_document("PUB-1", document("系统读取状态数据。通过反馈调整控制参数。"))
        self.assertEqual(self.repository.put("run", evidence), evidence)
        self.assertEqual(self.repository.put("run", evidence), evidence)
        self.assertEqual(self.repository.get("run", "PUB-1"), evidence)
        self.assertEqual(len(self.connection.sentences), 2)

    def test_unknown_publication_fails_before_write(self):
        self.connection.publication=False
        evidence=abstract_from_document("PUB-1", document("系统读取状态数据。通过反馈调整控制参数。"))
        with self.assertRaises(KeyError): self.repository.put("run", evidence)
        self.assertIsNone(self.connection.row)


if __name__ == "__main__": unittest.main()
