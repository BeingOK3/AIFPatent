from __future__ import annotations

import unittest

from landscape.clustering import (
    LandscapeClusteringError,
    single_document_cluster,
    validate_cluster_plan,
)
from landscape.schemas import (
    LandscapeCluster,
    LandscapeClusterPlan,
    LandscapeEvidenceRef,
    LandscapePatentAnalysis,
)


def analysis(publication: str) -> LandscapePatentAnalysis:
    return LandscapePatentAnalysis(
        publication_number=publication,
        prior_art="现有方案。",
        core_invention_points=["改进方案"],
        technical_keywords=["液冷"],
        evidence_refs=[
            LandscapeEvidenceRef(
                evidence_id="EV-000000000000000000000001",
                supports=["prior_art", "core_invention_point"],
            )
        ],
    )


class LandscapeClusteringTests(unittest.TestCase):
    def test_single_document_cluster_is_deterministic(self) -> None:
        plan = single_document_cluster(analysis("CN1A"))
        validate_cluster_plan(plan, {"CN1A"})
        self.assertEqual(plan.clusters[0].cluster_id, "CL-1")

    def test_duplicate_missing_and_unknown_members_fail_closed(self) -> None:
        duplicate = LandscapeClusterPlan(
            clusters=[
                LandscapeCluster(cluster_id="CL-1", name="A", summary="A", publication_numbers=["CN1A"]),
                LandscapeCluster(cluster_id="CL-2", name="B", summary="B", publication_numbers=["CN1A"]),
            ]
        )
        with self.assertRaisesRegex(LandscapeClusteringError, "more than one"):
            validate_cluster_plan(duplicate, {"CN1A", "CN2A"})
        incomplete = LandscapeClusterPlan(
            clusters=[
                LandscapeCluster(cluster_id="CL-1", name="A", summary="A", publication_numbers=["CN1A", "US9A1"])
            ]
        )
        with self.assertRaisesRegex(LandscapeClusteringError, "membership mismatch"):
            validate_cluster_plan(incomplete, {"CN1A", "CN2A"})


if __name__ == "__main__":
    unittest.main()
