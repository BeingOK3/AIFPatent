from __future__ import annotations

from idea.agent_schemas import register_agent_output_model
from idea.model_client import StructuredModelClient

from .schemas import LandscapeCluster, LandscapeClusterPlan, LandscapePatentAnalysis


CLUSTERER_NAME = "patent-landscape-clusterer"
CLUSTERER_PROMPT = """
Cluster the supplied patents by their technical solution, not by assignee or country. Use 2 to 8
clusters when the input size permits. Each publication_number must appear exactly once, and no new
publication_number may be introduced. Return cluster names and summaries in Simplified Chinese.
"""


class LandscapeClusteringError(RuntimeError):
    pass


def validate_cluster_plan(plan: LandscapeClusterPlan, expected_publications: set[str]) -> None:
    members = [publication for cluster in plan.clusters for publication in cluster.publication_numbers]
    if len(members) != len(set(members)):
        raise LandscapeClusteringError("a publication appears in more than one cluster")
    actual = set(members)
    if actual != expected_publications:
        missing = sorted(expected_publications - actual)
        unknown = sorted(actual - expected_publications)
        raise LandscapeClusteringError(
            f"cluster membership mismatch: missing={missing}, unknown={unknown}"
        )
    ids = [cluster.cluster_id for cluster in plan.clusters]
    if len(ids) != len(set(ids)):
        raise LandscapeClusteringError("cluster IDs must be unique")


def single_document_cluster(analysis: LandscapePatentAnalysis) -> LandscapeClusterPlan:
    keywords = analysis.technical_keywords[:10]
    name = keywords[0] if keywords else "单件专利"
    return LandscapeClusterPlan(
        clusters=[
            LandscapeCluster(
                cluster_id="CL-1",
                name=name,
                summary="当前成功精读集合仅包含一件专利，未进行跨专利主题归并。",
                keywords=keywords,
                publication_numbers=[analysis.publication_number],
            )
        ]
    )


class LandscapeClusteringService:
    def __init__(self, model: StructuredModelClient):
        register_agent_output_model(CLUSTERER_NAME, LandscapeClusterPlan)
        self.model = model

    async def cluster(
        self,
        analyses: dict[str, LandscapePatentAnalysis],
        metadata: dict[str, dict],
    ) -> LandscapeClusterPlan:
        if not analyses:
            raise LandscapeClusteringError("at least one successful analysis is required")
        if len(analyses) == 1:
            plan = single_document_cluster(next(iter(analyses.values())))
        else:
            result = await self.model.complete(
                CLUSTERER_NAME,
                system_prompt=CLUSTERER_PROMPT,
                input_payload={
                    "patents": [
                        {
                            "publication_number": publication,
                            "title": metadata.get(publication, {}).get("title", ""),
                            "abstract": metadata.get(publication, {}).get("abstract", ""),
                            "core_invention_points": analysis.core_invention_points,
                            "technical_keywords": analysis.technical_keywords,
                        }
                        for publication, analysis in analyses.items()
                    ]
                },
            )
            plan = result.output
            if not isinstance(plan, LandscapeClusterPlan):
                raise LandscapeClusteringError("landscape clusterer returned the wrong schema")
        validate_cluster_plan(plan, set(analyses))
        return plan
