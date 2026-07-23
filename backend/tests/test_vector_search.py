from __future__ import annotations

import asyncio
import unittest

from idea.embeddings import EmbeddingProfile
from idea.postgres_vector import PgVectorIndex
from idea.vector import (
    VectorHit,
    VectorSearchError,
    VectorSearchRequest,
    benchmark_vector_index,
)


ROW = (
    "chunk-1", "cv-1", "CN123A", "claims", "claim-1", "1",
    "independent", [], 0, 20, "cache eviction claim", "a" * 64,
    3, "claims-paragraphs-v1", 0.125,
)


class FakeCursor:
    def __init__(self, rows=(ROW,)) -> None:
        self.rows = rows
        self.executions = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def execute(self, query, parameters=None):
        self.executions.append((query, parameters))

    async def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, rows=(ROW,)) -> None:
        self.cursor_instance = FakeCursor(rows)

    def cursor(self):
        return self.cursor_instance

    async def close(self):
        return None


class PgVectorIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = EmbeddingProfile("fixture", "multilingual-v1", 2)

    def request(self, **updates):
        values = {
            "query_id": "vq-1",
            "embedding": (0.6, 0.8),
            "profile_id": self.profile.profile_id,
            "allowed_version_ids": ("cv-1", "cv-2"),
            "section_types": ("claims",),
            "limit": 30,
        }
        values.update(updates)
        return VectorSearchRequest(**values)

    def test_exact_search_is_parameterized_and_scoped_before_distance_sort(self) -> None:
        connection = FakeConnection()

        async def connect(_dsn):
            return connection

        hits = asyncio.run(
            PgVectorIndex(self.profile, "postgresql://test", connect=connect).search(
                self.request()
            )
        )

        self.assertEqual(len(connection.cursor_instance.executions), 3)
        self.assertIn("enable_indexscan = off", connection.cursor_instance.executions[0][0])
        sql, parameters = connection.cursor_instance.executions[2]
        self.assertIn("scoped AS MATERIALIZED", sql)
        self.assertIn("active_chunkers", sql)
        self.assertIn("version_id = ANY(%s)", sql)
        self.assertIn("ep.state = 'ACTIVE'", sql)
        self.assertIn("embedding <=> %s::vector", sql)
        self.assertNotIn("[0.599", sql)
        self.assertEqual(parameters[0], ["cv-1", "cv-2"])
        self.assertEqual(parameters[1], self.profile.profile_id)
        self.assertEqual(hits[0].rank, 1)
        self.assertEqual(hits[0].index_mode, "exact")
        self.assertEqual(hits[0].chunk.claim_number, 1)

    def test_profile_dimensions_normalization_and_scope_fail_closed(self) -> None:
        async def connect(_dsn):
            return FakeConnection()

        index = PgVectorIndex(self.profile, "postgresql://test", connect=connect)
        with self.assertRaisesRegex(VectorSearchError, "profile"):
            asyncio.run(index.search(self.request(profile_id="ep-other")))
        with self.assertRaisesRegex(VectorSearchError, "dimensions"):
            asyncio.run(index.search(self.request(embedding=(1.0,))))
        with self.assertRaisesRegex(VectorSearchError, "L2-normalized"):
            asyncio.run(index.search(self.request(embedding=(3.0, 4.0))))

        async def escaped(_dsn):
            row = list(ROW)
            row[1] = "cv-outside"
            return FakeConnection((tuple(row),))

        with self.assertRaisesRegex(VectorSearchError, "escaped"):
            asyncio.run(
                PgVectorIndex(self.profile, "postgresql://test", connect=escaped).search(
                    self.request()
                )
            )

    def test_request_requires_explicit_unique_version_scope(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires allowed Version"):
            self.request(allowed_version_ids=())
        with self.assertRaisesRegex(ValueError, "must be unique"):
            self.request(allowed_version_ids=("cv-1", "cv-1"))

    def test_benchmark_reports_exact_latency_without_vectors(self) -> None:
        class FakeIndex:
            async def search(self, request):
                return (
                    VectorHit(
                        query_id=request.query_id,
                        rank=1,
                        cosine_distance=0.1,
                        index_mode="exact",
                        chunk=None,
                    ),
                )

        result = asyncio.run(
            benchmark_vector_index(FakeIndex(), (self.request(), self.request(query_id="vq-2")))
        )
        self.assertEqual(result.request_count, 2)
        self.assertEqual(result.hit_count, 2)
        self.assertEqual(result.index_mode, "exact")
        self.assertGreaterEqual(result.p95_ms, 0)


if __name__ == "__main__":
    unittest.main()
