from __future__ import annotations

import asyncio
import os
import unittest
import uuid
from types import SimpleNamespace

from idea.embeddings import EmbeddingService
from idea.postgres_embeddings import PostgreSQLEmbeddingCache
from idea.postgres_vector import PgVectorIndex
from idea.vector import VectorSearchRequest, benchmark_vector_index


@unittest.skipUnless(
    os.environ.get("AIFPATENT_RUN_VECTOR_INTEGRATION") == "1",
    "set AIFPATENT_RUN_VECTOR_INTEGRATION=1 to test real pgvector retrieval",
)
class VectorIntegrationTests(unittest.TestCase):
    def test_real_cache_links_exact_scope_and_benchmark(self) -> None:
        asyncio.run(self._run_scenario())

    async def _run_scenario(self) -> None:
        import psycopg

        dsn = os.environ["AIFPATENT_POSTGRES_DSN"]
        suffix = uuid.uuid4().hex
        provider_name = f"integration-{suffix}"
        connection = await psycopg.AsyncConnection.connect(dsn)
        profile_id = None
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    "SELECT COUNT(*) FROM embedding_profiles WHERE state = 'ACTIVE'"
                )
                active_count = int((await cursor.fetchone())[0])
                if active_count:
                    self.skipTest("real deployment already has an active embedding profile")
                await cursor.execute(
                    """
                    SELECT c.chunk_id, c.version_id, c.text, c.text_hash
                    FROM patent_chunks c
                    JOIN patent_document_versions v ON v.version_id = c.version_id
                    WHERE v.state = 'READY'
                    ORDER BY c.version_id, c.chunk_id
                    """
                )
                candidates = await cursor.fetchall()
            selected = []
            for row in candidates:
                if selected and (
                    row[1] == selected[0][1] or row[3] == selected[0][3]
                ):
                    continue
                selected.append(row)
                if len(selected) == 2:
                    break
            self.assertEqual(len(selected), 2, "integration Corpus needs two distinct Versions")

            text_vectors = {
                str(selected[0][2]): [1.0, 0.0, 0.0],
                str(selected[1][2]): [0.0, 1.0, 0.0],
            }

            class FixtureProvider:
                provider = provider_name
                model = "exact-3d-v1"
                dimensions = 3

                def __init__(self):
                    self.calls = 0

                async def embed_documents(self, texts):
                    self.calls += 1
                    return [text_vectors[text] for text in texts]

                async def embed_query(self, text):
                    return [1.0, 0.0, 0.0]

            provider = FixtureProvider()
            cache = PostgreSQLEmbeddingCache(dsn)
            service = EmbeddingService(provider, cache, batch_size=8)
            profile_id = service.profile.profile_id
            chunks = [
                SimpleNamespace(chunk_id=str(row[0]), text=str(row[2]), text_hash=str(row[3]))
                for row in selected
            ]
            await service.index_chunks(chunks)
            await service.index_chunks(chunks)
            self.assertEqual(provider.calls, 1, "second indexing pass must reuse cache")
            await service.activate_for_chunks([chunk.chunk_id for chunk in chunks])

            index = PgVectorIndex(service.profile, dsn)
            first_request = VectorSearchRequest(
                query_id="integration-vector-1",
                embedding=await service.embed_query("first"),
                profile_id=profile_id,
                allowed_version_ids=(str(selected[0][1]),),
                limit=10,
            )
            hits = await index.search(first_request)
            self.assertEqual([hit.chunk.chunk_id for hit in hits], [str(selected[0][0])])
            self.assertTrue(all(hit.chunk.version_id == selected[0][1] for hit in hits))

            benchmark = await benchmark_vector_index(
                index,
                tuple(
                    VectorSearchRequest(
                        query_id=f"integration-benchmark-{number}",
                        embedding=first_request.embedding,
                        profile_id=profile_id,
                        allowed_version_ids=(str(selected[0][1]), str(selected[1][1])),
                        limit=2,
                    )
                    for number in range(5)
                ),
            )
            self.assertEqual(benchmark.request_count, 5)
            self.assertEqual(benchmark.hit_count, 10)
            self.assertLess(benchmark.p95_ms, 1000)
        finally:
            if profile_id:
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        DELETE FROM chunk_embeddings
                        WHERE embedding_id IN (
                            SELECT embedding_id FROM embedding_vectors WHERE profile_id = %s
                        )
                        """,
                        (profile_id,),
                    )
                    await cursor.execute(
                        "DELETE FROM embedding_vectors WHERE profile_id = %s",
                        (profile_id,),
                    )
                    await cursor.execute(
                        "DELETE FROM embedding_profiles WHERE profile_id = %s",
                        (profile_id,),
                    )
                await connection.commit()
            await connection.close()


if __name__ == "__main__":
    unittest.main()
