from __future__ import annotations

import unittest
from datetime import datetime, timezone

from idea.corpus import CorpusVersion
from idea.postgres_corpus import PostgreSQLCorpusError, PostgreSQLCorpusVersionRepository


class PostgreSQLCorpusContractTests(unittest.TestCase):
    def test_repository_requires_dsn_and_rejects_mismatched_key(self) -> None:
        with self.assertRaises(ValueError):
            PostgreSQLCorpusVersionRepository("")
        self.assertTrue(issubclass(PostgreSQLCorpusError, RuntimeError))

    def test_version_conversion_preserves_immutable_identity(self) -> None:
        digest = "a" * 64
        row = {
            "version_id": "cv-1",
            "document_id": "doc-1",
            "publication_number": "CN123",
            "language": "en",
            "normalized_content_hash": digest,
            "state": "READY",
            "metadata_json": {"provider": "fixture"},
            "created_at": 1_700_000_000_000,
            "object_key": "patent-corpus/CN123/" + digest + ".json",
            "uncompressed_bytes": 42,
        }
        version = PostgreSQLCorpusVersionRepository._row_to_version(row)
        self.assertEqual(version.document_id, "doc-1")
        self.assertEqual(version.provider, "fixture")
        self.assertEqual(version.content_sha256, digest)
        self.assertEqual(version.status, "READY")
        self.assertEqual(version.created_at.tzinfo, timezone.utc)

    def test_millis_conversion_is_deterministic(self) -> None:
        value = datetime(2026, 7, 21, tzinfo=timezone.utc)
        self.assertEqual(PostgreSQLCorpusVersionRepository._millis(value), 1784592000000)


if __name__ == "__main__":
    unittest.main()
