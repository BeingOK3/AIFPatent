from __future__ import annotations

from idea.agent_schemas import register_agent_output_model
from idea.model_client import StructuredModelClient

from .company_batches import CompanyAnalysisBatch
from .company_classification import (
    validate_company_technology_classification,
)
from .schemas import (
    CompanyTechnologyClassification,
    CompanyTechnologyProfile,
    CompanyTechnologyProfileNarrative,
)


COMPANY_PROFILE_AGENT_NAME = "landscape-company-technology-profiler"
COMPANY_PROFILE_PROMPT = """
Summarize one company's technical directions in Simplified Chinese using only the supplied,
already-validated categories and per-patent analyses. Return prose, direction labels, and honest
limitations only. Do not return or modify company IDs, category IDs, publication numbers, evidence
IDs, patent counts, dates, or statistics. Do not claim growth, decline, acceleration, or shifts;
time trends are computed and validated in a later cross-company stage.
"""


class CompanyTechnologyProfileError(RuntimeError):
    pass


class CompanyTechnologyProfileService:
    def __init__(self, model: StructuredModelClient):
        register_agent_output_model(
            COMPANY_PROFILE_AGENT_NAME,
            CompanyTechnologyProfileNarrative,
        )
        self.model = model

    async def build(
        self,
        batch: CompanyAnalysisBatch,
        classification: CompanyTechnologyClassification,
    ) -> CompanyTechnologyProfile:
        validate_company_technology_classification(batch, classification)
        if len(batch.items) == 1:
            category = classification.technology_categories[0]
            narrative = CompanyTechnologyProfileNarrative(
                overall_summary=category.summary,
                technology_directions=[category.name],
                limitations=batch.items[0].analysis.limitations,
            )
        else:
            completion = await self.model.complete(
                COMPANY_PROFILE_AGENT_NAME,
                system_prompt=COMPANY_PROFILE_PROMPT,
                input_payload={
                    "company_name": batch.company.canonical_name,
                    "categories": [
                        {
                            "name": category.name,
                            "summary": category.summary,
                            "keywords": category.keywords,
                        }
                        for category in classification.technology_categories
                    ],
                    "patent_analyses": [
                        {
                            "core_invention_points": (
                                item.analysis.core_invention_points
                            ),
                            "technical_problems_solved": (
                                item.analysis.technical_problems_solved
                            ),
                            "beneficial_effects": item.analysis.beneficial_effects,
                            "technical_keywords": item.analysis.technical_keywords,
                            "limitations": item.analysis.limitations,
                        }
                        for item in batch.items
                    ],
                },
            )
            narrative = completion.output
            if not isinstance(narrative, CompanyTechnologyProfileNarrative):
                raise CompanyTechnologyProfileError(
                    "company profiler returned the wrong schema"
                )
        profile = CompanyTechnologyProfile(
            overall_summary=narrative.overall_summary,
            technology_directions=narrative.technology_directions,
            technology_categories=classification.technology_categories,
            limitations=narrative.limitations,
        )
        validate_company_technology_profile(batch, classification, profile)
        return profile


def validate_company_technology_profile(
    batch: CompanyAnalysisBatch,
    classification: CompanyTechnologyClassification,
    profile: CompanyTechnologyProfile,
) -> None:
    validate_company_technology_classification(batch, classification)
    expected_categories = [
        category.model_dump(mode="json")
        for category in classification.technology_categories
    ]
    actual_categories = [
        category.model_dump(mode="json")
        for category in profile.technology_categories
    ]
    if actual_categories != expected_categories:
        raise CompanyTechnologyProfileError(
            "company profile must preserve validated categories exactly"
        )
    members = {
        publication
        for category in profile.technology_categories
        for publication in category.publication_numbers
    }
    if members != set(batch.publication_numbers):
        raise CompanyTechnologyProfileError(
            "company profile does not cover the analyzed company batch"
        )


__all__ = [
    "COMPANY_PROFILE_AGENT_NAME",
    "CompanyTechnologyProfileError",
    "CompanyTechnologyProfileService",
    "validate_company_technology_profile",
]
