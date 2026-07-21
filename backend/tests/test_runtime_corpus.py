from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from idea.config import load_config
from idea.corpus import PatentCorpusIngestService
from idea.chunks import PatentChunkPersistenceService
from idea.runtime import RuntimeConfigurationError, _build_corpus_ingest
from idea.postgres_corpus import (
    PostgreSQLCorpusPrerequisiteRepository,
    PostgreSQLPatentChunkRepository,
)


class RuntimeCorpusTests(unittest.TestCase):
    def setUp(self) -> None:
        config = load_config()
        features = config.features.model_copy(update={"patent_corpus": True})
        self.enabled = config.model_copy(update={"features": features})

    def test_disabled_feature_does_not_require_external_storage(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(_build_corpus_ingest(load_config()))

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


if __name__ == "__main__":
    unittest.main()
