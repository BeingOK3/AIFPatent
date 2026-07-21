from __future__ import annotations

import math
import unittest

from idea.ports import ObjectInfo, VectorHit, VectorQuery, VectorRecord


class InfrastructurePortContractTests(unittest.TestCase):
    def test_object_info_requires_content_hash(self) -> None:
        with self.assertRaises(ValueError):
            ObjectInfo(
                key="patent/v1.json.gz",
                size=12,
                sha256="not-a-hash",
                content_type="application/json",
                encoding="gzip",
            )

    def test_vector_query_must_be_version_scoped(self) -> None:
        with self.assertRaises(ValueError):
            VectorQuery(
                profile_id="multilingual-v1",
                embedding=(0.1, 0.2),
                allowed_version_ids=(),
                top_k=10,
            )

    def test_vector_values_and_ranks_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            VectorRecord(
                chunk_id="chunk-1",
                version_id="version-1",
                profile_id="multilingual-v1",
                embedding=(math.nan,),
            )
        with self.assertRaises(ValueError):
            VectorHit(chunk_id="chunk-1", version_id="version-1", score=0.9, rank=0)


if __name__ == "__main__":
    unittest.main()
