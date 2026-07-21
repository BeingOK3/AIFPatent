from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from idea.config import load_config
from idea.corpus import PatentCorpusIngestService
from idea.chunks import PatentChunkPersistenceService
from idea.hybrid import HybridRetriever
from idea.runtime import RuntimeConfigurationError, _build_corpus_ingest, build_initial_report_rag
from idea.report_rag import InitialReportRagService
from idea.postgres_corpus import (
    PostgreSQLCorpusPrerequisiteRepository,
    PostgreSQLPatentChunkRepository,
)


class RuntimeCorpusTests(unittest.TestCase):
    def setUp(self) -> None:
        config = load_config()
        disabled = config.features.model_copy(
            update={
                "patent_corpus": False,
                "initial_review_rag": False,
                "followup_rag": False,
            }
        )
        self.disabled = config.model_copy(update={"features": disabled})
        features = disabled.model_copy(update={"patent_corpus": True})
        self.enabled = config.model_copy(update={"features": features})

    def test_disabled_feature_does_not_require_external_storage(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(_build_corpus_ingest(self.disabled))

    def test_enabled_feature_fails_closed_when_storage_is_unconfigured(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeConfigurationError, "AIFPATENT_POSTGRES_DSN"):
                _build_corpus_ingest(self.enabled, database=object())

    def test_enabled_feature_builds_durable_corpus_adapters(self) -> None:
        environment = {
            "AIFPATENT_POSTGRES_DSN": "postgresql://user:secret@postgres/db",
            "AIFPATENT_S3_ENDPOINT_URL": "http://object-store:9000",
            "AIFPATENT_S3_BUCKET": "aifpatent-corpus",
            "AIFPATENT_S3_ACCESS_KEY": "local-user",
            "AIFPATENT_S3_SECRET_KEY": "local-secret",
        }
        with patch.dict(os.environ, environment, clear=True):
            service = _build_corpus_ingest(self.enabled, database=object())

        self.assertIsInstance(service, PatentCorpusIngestService)
        self.assertEqual(service.corpus.objects.bucket, "aifpatent-corpus")
        self.assertIsInstance(service.prerequisites, PostgreSQLCorpusPrerequisiteRepository)
        self.assertIsInstance(service.chunk_persistence, PatentChunkPersistenceService)
        self.assertIsInstance(
            service.chunk_persistence.repository,
            PostgreSQLPatentChunkRepository,
        )

    def test_initial_report_rag_builds_only_with_explicit_feature(self) -> None:
        features = self.enabled.features.model_copy(update={"initial_review_rag": True})
        config = self.enabled.model_copy(update={"features": features})
        with patch.dict(
            os.environ, {"AIFPATENT_POSTGRES_DSN": "postgresql://test"}, clear=True
        ):
            service = build_initial_report_rag(config)
        self.assertIsInstance(service, InitialReportRagService)
        self.assertIsInstance(
            service.retriever.chunk_repository, PostgreSQLPatentChunkRepository
        )
        self.assertIsInstance(service.retriever.hybrid_search, HybridRetriever)
        self.assertEqual(service.retriever.retriever_version, "hybrid-rrf-v1")
        self.assertIsNone(build_initial_report_rag(self.disabled))


if __name__ == "__main__":
    unittest.main()
