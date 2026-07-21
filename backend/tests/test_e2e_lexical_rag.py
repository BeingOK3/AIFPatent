from __future__ import annotations

import hashlib
import unittest

from tools.e2e_lexical_rag import (
    E2EValidationError,
    validate_postgres_sources,
    validate_report,
)


def report():
    excerpt = "1. verified claim"
    citation = {
        "context_id": "CTX-fixture", "feature_id": "F1", "alias": "C1",
        "chunk_id": "chunk-1", "version_id": "cv-1",
        "publication_number": "CN1A", "section_type": "claims",
        "section_label": "claim-1", "start_offset": 0,
        "end_offset": len(excerpt),
        "text_hash": hashlib.sha256(excerpt.encode()).hexdigest(),
        "excerpt": excerpt,
        "corpus_snapshot_hash": "a" * 64,
        "prompt_version": "prompt-v1",
        "retriever_version": "retriever-v1",
        "context_hash": "b" * 64,
    }
    return {
        "schema_version": "2.0", "run_id": "run-1", "citations": [citation],
        "rag_provenance": {
            "context_ids": ["CTX-fixture"],
            "corpus_snapshot_hashes": ["a" * 64],
            "prompt_versions": ["prompt-v1"],
            "retriever_versions": ["retriever-v1"],
            "context_hashes": ["b" * 64],
            "citation_text_hashes": [citation["text_hash"]],
        },
        "deep_review_documents": [{
            "feature_mappings": [{
                "feature_id": "F1", "status": "PARTIAL", "citations": [citation]
            }]
        }],
    }


class E2ELexicalRagValidationTests(unittest.TestCase):
    def test_valid_report_returns_secret_free_summary(self) -> None:
        result = validate_report(report())
        self.assertEqual(result["citation_count"], 1)
        self.assertNotIn("excerpt", result)

    def test_missing_or_tampered_citation_fails(self) -> None:
        missing = report()
        missing["citations"] = []
        with self.assertRaises(E2EValidationError):
            validate_report(missing)
        changed = report()
        changed["citations"][0]["excerpt"] = "changed"
        with self.assertRaisesRegex(E2EValidationError, "hash"):
            validate_report(changed)

    def test_postgres_verification_checks_source_and_model_selection(self) -> None:
        citation = report()["citations"][0]
        source = (
            citation["chunk_id"], citation["version_id"],
            citation["publication_number"], citation["section_type"],
            citation["section_label"], None, citation["start_offset"],
            citation["end_offset"], citation["excerpt"], citation["text_hash"],
            "READY", True, "READY",
        )

        class Cursor:
            def __init__(self):
                self.results = iter((
                    (
                        citation["context_id"], citation["corpus_snapshot_hash"],
                        citation["prompt_version"], citation["retriever_version"],
                        citation["context_hash"], [citation["version_id"]],
                    ),
                    ("READY", True, "READY"),
                    source,
                    (
                        citation["corpus_snapshot_hash"], citation["prompt_version"],
                        citation["retriever_version"], citation["context_hash"],
                    ),
                ))
            def __enter__(self): return self
            def __exit__(self, *args): return None
            def execute(self, sql, parameters): self.last = (sql, parameters)
            def fetchone(self): return next(self.results)

        class Connection:
            def __init__(self): self.cursor_instance = Cursor(); self.closed = False
            def cursor(self): return self.cursor_instance
            def close(self): self.closed = True

        connection = Connection()
        result = validate_postgres_sources(
            report(), "postgresql://hidden", connect=lambda _dsn: connection
        )
        self.assertEqual(result["postgres_verified_citation_count"], 1)
        self.assertEqual(result["postgres_verified_context_count"], 1)
        self.assertEqual(result["postgres_verified_version_count"], 1)
        self.assertTrue(connection.closed)

    def test_postgres_verification_rejects_changed_source(self) -> None:
        citation = report()["citations"][0]
        source = (
            citation["chunk_id"], citation["version_id"],
            citation["publication_number"], citation["section_type"],
            citation["section_label"], None, 1, citation["end_offset"],
            citation["excerpt"], citation["text_hash"], "READY", True, "READY",
        )

        class Cursor:
            def __init__(self):
                self.results = iter((
                    (
                        citation["context_id"], citation["corpus_snapshot_hash"],
                        citation["prompt_version"], citation["retriever_version"],
                        citation["context_hash"], [citation["version_id"]],
                    ),
                    ("READY", True, "READY"),
                    source,
                ))
            def __enter__(self): return self
            def __exit__(self, *args): return None
            def execute(self, sql, parameters): pass
            def fetchone(self): return next(self.results)

        class Connection:
            def cursor(self): return Cursor()
            def close(self): pass

        with self.assertRaisesRegex(E2EValidationError, "start_offset"):
            validate_postgres_sources(
                report(), "postgresql://hidden", connect=lambda _dsn: Connection()
            )

    def test_zero_citations_is_valid_when_every_mapping_is_negative(self) -> None:
        value = report()
        value["citations"] = []
        value["rag_provenance"]["citation_text_hashes"] = []
        value["deep_review_documents"][0]["feature_mappings"][0].update(
            {"status": "NOT_DISCLOSED", "citations": []}
        )
        result = validate_report(value)
        self.assertEqual(result["citation_count"], 0)

    def test_zero_citations_still_verifies_context_and_version(self) -> None:
        value = report()
        value["citations"] = []
        value["rag_provenance"]["citation_text_hashes"] = []
        value["deep_review_documents"][0]["feature_mappings"][0].update(
            {"status": "NOT_DISCLOSED", "citations": []}
        )

        class Cursor:
            def __init__(self):
                self.results = iter((
                    (
                        "CTX-fixture", "a" * 64, "prompt-v1", "retriever-v1",
                        "b" * 64, ["cv-1"],
                    ),
                    ("READY", True, "READY"),
                ))
            def __enter__(self): return self
            def __exit__(self, *args): return None
            def execute(self, sql, parameters): pass
            def fetchone(self): return next(self.results)

        class Connection:
            def cursor(self): return Cursor()
            def close(self): pass

        result = validate_postgres_sources(
            value, "postgresql://hidden", connect=lambda _dsn: Connection()
        )
        self.assertEqual(result["postgres_verified_citation_count"], 0)
        self.assertEqual(result["postgres_verified_context_count"], 1)
        self.assertEqual(result["postgres_verified_version_count"], 1)


if __name__ == "__main__":
    unittest.main()
