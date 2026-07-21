from __future__ import annotations

import asyncio
import unittest

from idea.embeddings import CachedEmbedding, EmbeddingError, EmbeddingProfile
from idea.postgres_embeddings import PostgreSQLEmbeddingCache


class FakeCursor:
    def __init__(self) -> None:
        self.executions = []
        self.last_sql = ""

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def execute(self, query, parameters):
        self.last_sql = query
        self.executions.append((query, parameters))

    async def fetchone(self):
        if "embedding_profiles" in self.last_sql:
            return ("fixture", "multilingual-v1", 2, "l2")
        return ("[0.6,0.8]", 1.0)

    async def fetchall(self):
        return [("a" * 64, "ep-test", "[0.6,0.8]", 1.0)]


class FakeConnection:
    def __init__(self) -> None:
        self.cursor_instance = FakeCursor()
        self.committed = False
        self.rolled_back = False

    def cursor(self):
        return self.cursor_instance

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True

    async def close(self):
        return None


class PostgreSQLEmbeddingCacheTests(unittest.TestCase):
    def test_profile_and_vector_writes_are_parameterized_and_idempotent(self) -> None:
        connection = FakeConnection()

        async def connect(_dsn):
            return connection

        cache = PostgreSQLEmbeddingCache("postgresql://test", connect=connect)
        profile = EmbeddingProfile("fixture", "multilingual-v1", 2)
        asyncio.run(cache.ensure_profile(profile))
        record = CachedEmbedding("a" * 64, profile.profile_id, (0.6, 0.8), 1.0)
        asyncio.run(cache.put_many((record,)))

        profile_sql, profile_parameters = connection.cursor_instance.executions[0]
        vector_sql, vector_parameters = connection.cursor_instance.executions[1]
        self.assertIn("ON CONFLICT (profile_id)", profile_sql)
        self.assertEqual(profile_parameters[0], profile.profile_id)
        self.assertIn("%s::vector", vector_sql)
        self.assertNotIn("[0.6,0.8]", vector_sql)
        self.assertEqual(vector_parameters[3], "[0.59999999999999998,0.80000000000000004]")
        self.assertTrue(connection.committed)

    def test_get_many_is_scoped_by_profile_and_hashes(self) -> None:
        connection = FakeConnection()

        async def connect(_dsn):
            return connection

        result = asyncio.run(
            PostgreSQLEmbeddingCache("postgresql://test", connect=connect).get_many(
                "ep-test", ("a" * 64,)
            )
        )

        sql, parameters = connection.cursor_instance.executions[0]
        self.assertIn("profile_id = %s", sql)
        self.assertIn("text_hash = ANY(%s)", sql)
        self.assertEqual(parameters, ("ep-test", ["a" * 64]))
        self.assertEqual(result["a" * 64].embedding, (0.6, 0.8))

    def test_profile_metadata_conflict_rolls_back(self) -> None:
        connection = FakeConnection()

        async def connect(_dsn):
            return connection

        async def conflicting_fetchone():
            return ("fixture", "other-model", 2, "l2")

        connection.cursor_instance.fetchone = conflicting_fetchone
        cache = PostgreSQLEmbeddingCache("postgresql://test", connect=connect)
        with self.assertRaisesRegex(EmbeddingError, "conflicts"):
            asyncio.run(cache.ensure_profile(EmbeddingProfile("fixture", "multilingual-v1", 2)))
        self.assertTrue(connection.rolled_back)


if __name__ == "__main__":
    unittest.main()
