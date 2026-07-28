from __future__ import annotations

from idea.agent_schemas import register_agent_output_model
from idea.model_client import StructuredModelClient

from .company_batches import CompanyAnalysisBatch
from .schemas import (
    CompanyTechnologyCategory,
    CompanyTechnologyClassification,
)


COMPANY_CLASSIFIER_NAME = "landscape-company-technology-classifier"
COMPANY_CLASSIFIER_PROMPT = """
Classify patents belonging to exactly one company by technical solution. Return Simplified Chinese
names and summaries. Every supplied publication_number must appear in exactly one primary category.
Do not invent publications or evidence IDs. Each category must cite evidence from every member
patent. Categories describe technology, never corporate structure, geography, or filing volume.
Use the supplied per-patent analyses only; do not add facts absent from them.
"""


class CompanyTechnologyClassificationError(RuntimeError):
    pass


class CompanyTechnologyClassificationService:
    def __init__(self, model: StructuredModelClient):
        register_agent_output_model(
            COMPANY_CLASSIFIER_NAME,
            CompanyTechnologyClassification,
        )
        self.model = model

    async def classify(
        self,
        batch: CompanyAnalysisBatch,
    ) -> CompanyTechnologyClassification:
        if not batch.items:
            raise CompanyTechnologyClassificationError(
                "company classification requires at least one analyzed patent"
            )
        if len(batch.items) == 1:
            result = _single_patent_classification(batch)
        else:
            completion = await self.model.complete(
                COMPANY_CLASSIFIER_NAME,
                system_prompt=COMPANY_CLASSIFIER_PROMPT,
                input_payload={
                    "company_name": batch.company.canonical_name,
                    "patents": [
                        {
                            "publication_number": item.publication_number,
                            "prior_art": item.analysis.prior_art,
                            "prior_art_problems": item.analysis.prior_art_problems,
                            "core_invention_points": item.analysis.core_invention_points,
                            "technical_problems_solved": (
                                item.analysis.technical_problems_solved
                            ),
                            "beneficial_effects": item.analysis.beneficial_effects,
                            "technical_keywords": item.analysis.technical_keywords,
                            "evidence_refs": [
                                reference.model_dump(mode="json")
                                for reference in item.analysis.evidence_refs
                            ],
                            "limitations": item.analysis.limitations,
                        }
                        for item in batch.items
                    ],
                },
            )
            result = completion.output
            if not isinstance(result, CompanyTechnologyClassification):
                raise CompanyTechnologyClassificationError(
                    "company classifier returned the wrong schema"
                )
        result = _canonicalize_category_ids(batch, result)
        validate_company_technology_classification(batch, result)
        return result


def validate_company_technology_classification(
    batch: CompanyAnalysisBatch,
    result: CompanyTechnologyClassification,
) -> None:
    expected = set(batch.publication_numbers)
    members = [
        publication
        for category in result.technology_categories
        for publication in category.publication_numbers
    ]
    duplicates = sorted(
        publication
        for publication in set(members)
        if members.count(publication) > 1
    )
    missing = sorted(expected - set(members))
    invented = sorted(set(members) - expected)
    if duplicates or missing or invented:
        raise CompanyTechnologyClassificationError(
            "company categories must cover the batch exactly "
            f"(duplicates={duplicates}, missing={missing}, outside_batch={invented})"
        )

    evidence_by_publication = {
        item.publication_number: {
            reference.evidence_id
            for reference in item.analysis.evidence_refs
        }
        for item in batch.items
    }
    for category in result.technology_categories:
        allowed = set().union(
            *(
                evidence_by_publication[publication]
                for publication in category.publication_numbers
            )
        )
        cited = set(category.evidence_ids)
        unknown = sorted(cited - allowed)
        uncited_members = sorted(
            publication
            for publication in category.publication_numbers
            if not (cited & evidence_by_publication[publication])
        )
        if unknown or uncited_members:
            raise CompanyTechnologyClassificationError(
                f"category evidence mismatch: {category.category_id} "
                f"(unknown={unknown}, uncited_members={uncited_members})"
            )


def _single_patent_classification(
    batch: CompanyAnalysisBatch,
) -> CompanyTechnologyClassification:
    item = batch.items[0]
    analysis = item.analysis
    keywords = analysis.technical_keywords[:10]
    name = keywords[0] if keywords else "单件专利技术方案"
    summary = "；".join(analysis.core_invention_points)
    return CompanyTechnologyClassification(
        technology_categories=[
            CompanyTechnologyCategory(
                category_id=_category_id(batch, 1),
                name=name,
                summary=summary,
                keywords=keywords,
                publication_numbers=[item.publication_number],
                evidence_ids=sorted(
                    {
                        reference.evidence_id
                        for reference in analysis.evidence_refs
                    }
                ),
            )
        ]
    )


def _canonicalize_category_ids(
    batch: CompanyAnalysisBatch,
    result: CompanyTechnologyClassification,
) -> CompanyTechnologyClassification:
    categories = sorted(
        result.technology_categories,
        key=lambda category: (
            min(category.publication_numbers),
            category.name.casefold(),
            category.name,
        ),
    )
    return CompanyTechnologyClassification(
        technology_categories=[
            category.model_copy(
                update={"category_id": _category_id(batch, index)}
            )
            for index, category in enumerate(categories, start=1)
        ]
    )


def _category_id(batch: CompanyAnalysisBatch, index: int) -> str:
    token = (
        batch.company_id.removeprefix("CO-")
        if batch.company_id != "UNKNOWN"
        else "UNKNOWN"
    )
    return f"TC-{token}-{index:02d}"


__all__ = [
    "COMPANY_CLASSIFIER_NAME",
    "CompanyTechnologyClassificationError",
    "CompanyTechnologyClassificationService",
    "validate_company_technology_classification",
]
