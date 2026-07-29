from __future__ import annotations

from collections.abc import Mapping
from datetime import date

from idea.agent_schemas import register_agent_output_model
from idea.model_client import StructuredModelClient

from .schemas import (
    CompanyTechnologyProfile,
    CrossCompanyTrend,
    CrossCompanyTrendAnalysis,
    CrossCompanyTrendProposal,
    CrossCompanyTrendProposalAnalysis,
    LandscapePatentAnalysis,
    TrendTimeBasis,
)
from .schemas import LandscapeDirectionFingerprint


CROSS_COMPANY_TREND_AGENT_NAME = "landscape-cross-company-trend-analyzer"
CROSS_COMPANY_TREND_PROMPT = """
Compare validated company technology profiles and propose evidence-bound trends in Simplified
Chinese. Use only supplied company IDs, publications, evidence IDs, and program-computed time
buckets. Every trend must involve at least two companies and two patents. Return no trend IDs,
exact dates, counts, ratios, or slopes. Use directional labels only when the supplied patents span
enough time buckets; otherwise use UNCERTAIN and describe an observed direction without claiming
growth, decline, acceleration, emergence, stability, or a shift.
"""

_TEMPORAL_DIRECTIONS = {
    "EMERGING",
    "GROWING",
    "DECLINING",
    "SHIFTING",
    "ACCELERATING",
    "STABLE",
}


class CrossCompanyTrendValidationError(RuntimeError):
    pass


class CrossCompanyTrendService:
    def __init__(self, model: StructuredModelClient):
        register_agent_output_model(
            CROSS_COMPANY_TREND_AGENT_NAME,
            CrossCompanyTrendProposalAnalysis,
        )
        self.model = model

    async def analyze(
        self,
        *,
        profiles: Mapping[str, CompanyTechnologyProfile],
        analyses: Mapping[str, LandscapePatentAnalysis],
        publication_dates: Mapping[str, date],
        time_basis: TrendTimeBasis,
        fingerprints: Mapping[str, LandscapeDirectionFingerprint] | None = None,
        minimum_patents_for_time_trend: int = 3,
    ) -> CrossCompanyTrendAnalysis:
        if fingerprints:
            context = _build_lightweight_trend_context(
                profiles=profiles,
                fingerprints=fingerprints,
                publication_dates=publication_dates,
                time_basis=time_basis,
            )
        else:
            context = _build_trend_context(
                profiles=profiles,
                analyses=analyses,
                publication_dates=publication_dates,
                time_basis=time_basis,
            )
        if len(profiles) < 2:
            return CrossCompanyTrendAnalysis(
                overall_summary="当前成功分析结果不足两家公司，无法进行跨公司比较。",
                limitations=["至少需要两家公司才能生成跨公司趋势。"],
            )
        completion = await self.model.complete(
            CROSS_COMPANY_TREND_AGENT_NAME,
            system_prompt=CROSS_COMPANY_TREND_PROMPT,
            input_payload=context["model_payload"],
        )
        proposal = completion.output
        if not isinstance(proposal, CrossCompanyTrendProposalAnalysis):
            raise CrossCompanyTrendValidationError(
                "cross-company trend analyzer returned the wrong schema"
            )
        validate_cross_company_trend_proposal(
            proposal,
            company_by_publication=context["company_by_publication"],
            evidence_by_publication=context["evidence_by_publication"],
            bucket_by_publication=context["bucket_by_publication"],
            minimum_patents_for_time_trend=minimum_patents_for_time_trend,
        )
        trends = [
            CrossCompanyTrend(
                trend_id=f"TR-{index:02d}",
                name=trend.name,
                summary=trend.summary,
                direction=trend.direction,
                company_ids=sorted(trend.company_ids),
                publication_numbers=sorted(trend.publication_numbers),
                evidence_ids=sorted(trend.evidence_ids),
                time_basis=time_basis,
            )
            for index, trend in enumerate(
                sorted(
                    proposal.trends,
                    key=lambda trend: (
                        min(trend.publication_numbers),
                        trend.name.casefold(),
                        trend.name,
                    ),
                ),
                start=1,
            )
        ]
        return CrossCompanyTrendAnalysis(
            overall_summary=proposal.overall_summary,
            common_directions=proposal.common_directions,
            differentiated_directions=proposal.differentiated_directions,
            trends=trends,
            limitations=proposal.limitations,
        )


def validate_cross_company_trend_proposal(
    proposal: CrossCompanyTrendProposalAnalysis,
    *,
    company_by_publication: Mapping[str, str],
    evidence_by_publication: Mapping[str, set[str]],
    bucket_by_publication: Mapping[str, str],
    minimum_patents_for_time_trend: int,
) -> None:
    if minimum_patents_for_time_trend < 2:
        raise ValueError("minimum patents for a time trend must be at least 2")
    for trend in proposal.trends:
        publications = set(trend.publication_numbers)
        unknown_publications = sorted(
            publications - set(company_by_publication)
        )
        if unknown_publications:
            raise CrossCompanyTrendValidationError(
                f"trend references unknown publications: {unknown_publications}"
            )
        expected_companies = {
            company_by_publication[publication]
            for publication in publications
        }
        if set(trend.company_ids) != expected_companies:
            raise CrossCompanyTrendValidationError(
                "trend company IDs must exactly match publication owners"
            )
        allowed_evidence = set().union(
            *(evidence_by_publication[publication] for publication in publications)
        )
        cited = set(trend.evidence_ids)
        unknown_evidence = sorted(cited - allowed_evidence)
        uncited_publications = sorted(
            publication
            for publication in publications
            if not (cited & evidence_by_publication[publication])
        )
        if unknown_evidence or uncited_publications:
            raise CrossCompanyTrendValidationError(
                "trend evidence must be owned by every referenced publication "
                f"(unknown={unknown_evidence}, uncited={uncited_publications})"
            )
        buckets = {
            bucket_by_publication[publication]
            for publication in publications
        }
        if trend.direction in _TEMPORAL_DIRECTIONS and (
            len(publications) < minimum_patents_for_time_trend
            or len(buckets) < 2
        ):
            raise CrossCompanyTrendValidationError(
                "directional trend lacks sufficient patents or time buckets"
            )


def _build_trend_context(
    *,
    profiles: Mapping[str, CompanyTechnologyProfile],
    analyses: Mapping[str, LandscapePatentAnalysis],
    publication_dates: Mapping[str, date],
    time_basis: TrendTimeBasis,
) -> dict:
    company_by_publication: dict[str, str] = {}
    for company_id, profile in profiles.items():
        for category in profile.technology_categories:
            for publication in category.publication_numbers:
                owner = company_by_publication.setdefault(
                    publication, company_id
                )
                if owner != company_id:
                    raise CrossCompanyTrendValidationError(
                        f"publication belongs to multiple company profiles: {publication}"
                    )
    publications = set(company_by_publication)
    if publications != set(analyses):
        raise CrossCompanyTrendValidationError(
            "company profiles and successful analyses must cover the same publications"
        )
    if publications != set(publication_dates):
        raise CrossCompanyTrendValidationError(
            "publication dates must cover the trend input exactly"
        )
    for publication, published in publication_dates.items():
        if published < time_basis.start or published > time_basis.end:
            raise CrossCompanyTrendValidationError(
                f"publication date is outside time basis: {publication}"
            )
    evidence_by_publication = {
        publication: {
            reference.evidence_id
            for reference in analysis.evidence_refs
        }
        for publication, analysis in analyses.items()
    }
    for company_id, profile in profiles.items():
        for category in profile.technology_categories:
            allowed = set().union(
                *(
                    evidence_by_publication[publication]
                    for publication in category.publication_numbers
                )
            )
            cited = set(category.evidence_ids)
            if not cited <= allowed:
                raise CrossCompanyTrendValidationError(
                    f"company profile contains invalid evidence: {company_id}"
                )
            if any(
                not (cited & evidence_by_publication[publication])
                for publication in category.publication_numbers
            ):
                raise CrossCompanyTrendValidationError(
                    f"company profile leaves a member patent uncited: {company_id}"
                )
    bucket_by_publication = {
        publication: _bucket_key(published, time_basis.bucket)
        for publication, published in publication_dates.items()
    }
    return {
        "company_by_publication": company_by_publication,
        "evidence_by_publication": evidence_by_publication,
        "bucket_by_publication": bucket_by_publication,
        "model_payload": {
            "companies": [
                {
                    "company_id": company_id,
                    "overall_summary": profile.overall_summary,
                    "technology_directions": profile.technology_directions,
                    "categories": [
                        {
                            "name": category.name,
                            "summary": category.summary,
                            "publication_numbers": category.publication_numbers,
                            "evidence_ids": category.evidence_ids,
                        }
                        for category in profile.technology_categories
                    ],
                }
                for company_id, profile in sorted(profiles.items())
            ],
            "patents": [
                {
                    "publication_number": publication,
                    "company_id": company_by_publication[publication],
                    "time_bucket": bucket_by_publication[publication],
                    "evidence_ids": sorted(
                        evidence_by_publication[publication]
                    ),
                }
                for publication in sorted(publications)
            ],
        },
    }


def _build_lightweight_trend_context(
    *,
    profiles: Mapping[str, CompanyTechnologyProfile],
    fingerprints: Mapping[str, LandscapeDirectionFingerprint],
    publication_dates: Mapping[str, date],
    time_basis: TrendTimeBasis,
) -> dict:
    company_by_publication: dict[str, str] = {}
    for company_id, profile in profiles.items():
        for category in profile.technology_categories:
            for publication in category.publication_numbers:
                owner = company_by_publication.setdefault(
                    publication, company_id
                )
                if owner != company_id:
                    raise CrossCompanyTrendValidationError(
                        "publication belongs to multiple company profiles: "
                        f"{publication}"
                    )
    publications = set(fingerprints)
    if publications != set(company_by_publication):
        raise CrossCompanyTrendValidationError(
            "lightweight company profiles and fingerprints must cover the same publications"
        )
    if publications != set(publication_dates):
        raise CrossCompanyTrendValidationError(
            "lightweight publication dates must cover the trend input exactly"
        )
    for publication, fingerprint in fingerprints.items():
        if fingerprint.company_id != company_by_publication[publication]:
            raise CrossCompanyTrendValidationError(
                "lightweight fingerprint company does not match company profile: "
                f"{publication}"
            )
    for publication, published in publication_dates.items():
        if published < time_basis.start or published > time_basis.end:
            raise CrossCompanyTrendValidationError(
                f"publication date is outside time basis: {publication}"
            )
    evidence_by_publication = {
        publication: {item.evidence_id for item in fingerprint.evidence}
        for publication, fingerprint in fingerprints.items()
    }
    bucket_by_publication = {
        publication: _bucket_key(published, time_basis.bucket)
        for publication, published in publication_dates.items()
    }
    return {
        "company_by_publication": company_by_publication,
        "evidence_by_publication": evidence_by_publication,
        "bucket_by_publication": bucket_by_publication,
        "model_payload": {
            "companies": [
                {
                    "company_id": company_id,
                    "overall_summary": profile.overall_summary,
                    "technology_directions": profile.technology_directions,
                    "categories": [
                        {
                            "name": category.name,
                            "summary": category.summary,
                            "publication_numbers": category.publication_numbers,
                            "evidence_ids": category.evidence_ids,
                        }
                        for category in profile.technology_categories
                    ],
                }
                for company_id, profile in sorted(profiles.items())
            ],
            "patents": [
                {
                    "publication_number": publication,
                    "company_id": company_by_publication[publication],
                    "title": fingerprints[publication].title,
                    "technical_keywords": fingerprints[publication].technical_keywords,
                    "time_bucket": bucket_by_publication[publication],
                    "evidence_ids": sorted(evidence_by_publication[publication]),
                }
                for publication in sorted(publications)
            ],
        },
    }


def _bucket_key(value: date, bucket: str) -> str:
    if bucket == "MONTH":
        return f"{value.year:04d}-{value.month:02d}"
    quarter = (value.month - 1) // 3 + 1
    return f"{value.year:04d}-Q{quarter}"


__all__ = [
    "CROSS_COMPANY_TREND_AGENT_NAME",
    "CrossCompanyTrendService",
    "CrossCompanyTrendValidationError",
    "validate_cross_company_trend_proposal",
]
