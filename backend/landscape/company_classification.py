from __future__ import annotations

from idea.agent_schemas import register_agent_output_model
from idea.model_client import StructuredModelClient

from .company_batches import CompanyAnalysisBatch
from .schemas import (
    CompanyTechnologyCategory,
    CompanyTechnologyClassification,
    CompanyTechnologyClassificationDraft,
)
from .schemas import LandscapeDirectionFingerprint, NormalizedCompany


COMPANY_CLASSIFIER_NAME = "landscape-company-technology-classifier"
LIGHTWEIGHT_COMPANY_CLASSIFIER_NAME = "landscape-company-lightweight-classifier"
COMPANY_CATEGORY_CONSOLIDATOR_NAME = "landscape-company-category-consolidator"
COMPANY_CLASSIFIER_PROMPT = """
Classify patents belonging to exactly one company by technical solution. Return Simplified Chinese
names and summaries. Every supplied publication_number must appear in exactly one primary category.
Do not invent publications or evidence IDs. Each category must cite evidence from every member
patent. Categories describe technology, never corporate structure, geography, or filing volume.
Use the supplied per-patent analyses only; do not add facts absent from them.
"""

COMPANY_CATEGORY_CONSOLIDATOR_PROMPT = """
You are consolidating already validated, lightweight technology categories for one
company. Return Simplified Chinese. Reduce the supplied source categories to no
more than 20 coherent technology categories. Merge only categories that have a
compatible technical meaning; do not add facts, companies, dates, publication
numbers, or evidence IDs.

Every supplied publication_number must occur in exactly one returned category.
For every returned member publication, retain at least one of its supplied
evidence_ids in that category. A returned category may contain the union of the
source categories' publication numbers, keywords, and evidence IDs. category_id
is a temporary value and will be replaced by the program, but it must match the
required TC-* format. Keep the result concise and do not create categories for
corporate structure, geography, or filing volume.
"""


class CompanyTechnologyClassificationError(RuntimeError):
    pass


class CompanyTechnologyClassificationService:
    def __init__(self, model: StructuredModelClient):
        register_agent_output_model(
            COMPANY_CLASSIFIER_NAME,
            CompanyTechnologyClassificationDraft,
        )
        register_agent_output_model(
            LIGHTWEIGHT_COMPANY_CLASSIFIER_NAME,
            CompanyTechnologyClassificationDraft,
        )
        register_agent_output_model(
            COMPANY_CATEGORY_CONSOLIDATOR_NAME,
            CompanyTechnologyClassificationDraft,
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
            if not isinstance(
                result,
                (CompanyTechnologyClassification, CompanyTechnologyClassificationDraft),
            ):
                raise CompanyTechnologyClassificationError(
                    "company classifier returned the wrong schema"
                )
        result = _canonicalize_category_ids(batch, result)
        validate_company_technology_classification(batch, result)
        return result

    async def classify_fingerprints(
        self,
        *,
        company: NormalizedCompany,
        fingerprints: list[LandscapeDirectionFingerprint],
    ) -> CompanyTechnologyClassification:
        """Classify a company's full lightweight direction set.

        This path intentionally does not require deep patent analyses. It is
        the input used for company-level landscape trends.
        """
        if not fingerprints:
            raise CompanyTechnologyClassificationError(
                "company fingerprint classification requires at least one patent"
            )
        batch_size = 8
        if len(fingerprints) > batch_size:
            partial: list[CompanyTechnologyClassification] = []
            ordered = sorted(
                fingerprints,
                key=lambda item: item.publication_number,
            )
            for offset in range(0, len(ordered), batch_size):
                partial.append(
                    await self.classify_fingerprints(
                        company=company,
                        fingerprints=ordered[offset : offset + batch_size],
                    )
                )
            merged: dict[str, CompanyTechnologyCategory] = {}
            for result in partial:
                for category in result.technology_categories:
                    key = category.name.casefold()
                    existing = merged.get(key)
                    if existing is None:
                        merged[key] = category
                    else:
                        merged[key] = existing.model_copy(
                            update={
                                "summary": f"{existing.summary}；{category.summary}",
                                "keywords": list(
                                    dict.fromkeys(
                                        [*existing.keywords, *category.keywords]
                                    )
                                )[:30],
                                "publication_numbers": sorted(
                                    set(
                                        [
                                            *existing.publication_numbers,
                                            *category.publication_numbers,
                                        ]
                                    )
                                ),
                                "evidence_ids": sorted(
                                    set(
                                        [*existing.evidence_ids, *category.evidence_ids]
                                    )
                                ),
                            }
                        )
            categories = list(merged.values())
            if len(categories) > 20:
                # A full-company result has a deliberately bounded shape.
                # Do not loosen the strict schema merely because several
                # otherwise valid batches produced distinct labels.  Instead,
                # give the model the already verified category memberships and
                # make it perform a controlled semantic consolidation.
                result = await self._consolidate_fingerprint_categories(
                    company=company,
                    categories=categories,
                )
            else:
                result = _canonicalize_fingerprint_category_ids(
                    company.company_id,
                    # Every recursive batch has already received program-owned
                    # IDs starting at ``01``.  Before the whole-company merge is
                    # renumbered those IDs can collide across batches, so keep
                    # this intermediate representation permissive.
                    CompanyTechnologyClassificationDraft(
                        technology_categories=categories
                    ),
                )
            self._validate_fingerprint_result(result, fingerprints)
            return result
        expected = {item.publication_number for item in fingerprints}
        evidence_by_publication = {
            item.publication_number: {evidence.evidence_id for evidence in item.evidence}
            for item in fingerprints
        }
        if len(fingerprints) == 1:
            item = fingerprints[0]
            result = CompanyTechnologyClassification(
                technology_categories=[
                    CompanyTechnologyCategory(
                        category_id="TC-TEMP-01",
                        name=item.technical_keywords[0]
                        if item.technical_keywords
                        else "未细分技术方向",
                        summary=item.title or "基于检索证据的单件技术方向",
                        keywords=item.technical_keywords[:10],
                        publication_numbers=[item.publication_number],
                        evidence_ids=sorted(evidence_by_publication[item.publication_number]),
                    )
                ]
            )
        else:
            completion = await self.model.complete(
                LIGHTWEIGHT_COMPANY_CLASSIFIER_NAME,
                system_prompt=(
                    "Classify every supplied patent into technology categories using only "
                    "the lightweight title, abstract/snippet evidence, and keywords. "
                    "Return Simplified Chinese. Every publication must appear exactly once "
                    "and every category must cite evidence owned by its members. Do not "
                    "invent statistics, dates, companies, or publication numbers."
                ),
                input_payload={
                    "company_name": company.canonical_name,
                    "patents": [
                        {
                            "publication_number": item.publication_number,
                            "title": item.title,
                            "publication_date": item.publication_date,
                            "technical_keywords": item.technical_keywords,
                            "evidence": [
                                evidence.model_dump(mode="json")
                                for evidence in item.evidence
                            ],
                        }
                        for item in fingerprints
                    ],
                },
            )
            result = completion.output
            if not isinstance(result, CompanyTechnologyClassificationDraft):
                raise CompanyTechnologyClassificationError(
                    "company fingerprint classifier returned the wrong schema"
                )
        result = _canonicalize_fingerprint_category_ids(company.company_id, result)
        _validate_fingerprint_classification(
            result,
            expected=expected,
            evidence_by_publication=evidence_by_publication,
        )
        return result

    async def _consolidate_fingerprint_categories(
        self,
        *,
        company: NormalizedCompany,
        categories: list[CompanyTechnologyCategory],
    ) -> CompanyTechnologyClassification:
        """Merge an oversized set without changing the final schema contract.

        The input was produced by validated recursive batches.  We deliberately
        send only category-level, lightweight material: this phase must not
        trigger a full-text patent read or manufacture new evidence.
        """
        completion = await self.model.complete(
            COMPANY_CATEGORY_CONSOLIDATOR_NAME,
            system_prompt=COMPANY_CATEGORY_CONSOLIDATOR_PROMPT,
            input_payload={
                "company_name": company.canonical_name,
                "max_categories": 20,
                "source_categories": [
                    {
                        "name": category.name,
                        # A bounded category summary keeps a 100--200 patent
                        # run within the lightweight-model context budget.
                        "summary": category.summary[:600],
                        "keywords": category.keywords[:12],
                        "publication_numbers": category.publication_numbers,
                        "evidence_ids": category.evidence_ids,
                    }
                    for category in categories
                ],
            },
        )
        result = completion.output
        if not isinstance(result, CompanyTechnologyClassificationDraft):
            raise CompanyTechnologyClassificationError(
                "company category consolidator returned the wrong schema"
            )
        return _canonicalize_fingerprint_category_ids(company.company_id, result)

    @staticmethod
    def _validate_fingerprint_result(
        result: CompanyTechnologyClassification,
        fingerprints: list[LandscapeDirectionFingerprint],
    ) -> None:
        _validate_fingerprint_classification(
            result,
            expected={item.publication_number for item in fingerprints},
            evidence_by_publication={
                item.publication_number: {
                    evidence.evidence_id for evidence in item.evidence
                }
                for item in fingerprints
            },
        )


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
    result: CompanyTechnologyClassification
    | CompanyTechnologyClassificationDraft,
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


def _canonicalize_fingerprint_category_ids(
    company_id: str,
    result: CompanyTechnologyClassification
    | CompanyTechnologyClassificationDraft,
) -> CompanyTechnologyClassification:
    categories = sorted(
        result.technology_categories,
        key=lambda category: (min(category.publication_numbers), category.name.casefold()),
    )
    token = company_id.removeprefix("CO-") if company_id != "UNKNOWN" else "UNKNOWN"
    return CompanyTechnologyClassification(
        technology_categories=[
            category.model_copy(
                update={"category_id": f"TC-{token}-{index:02d}"}
            )
            for index, category in enumerate(categories, start=1)
        ]
    )


def _validate_fingerprint_classification(
    result: CompanyTechnologyClassification,
    *,
    expected: set[str],
    evidence_by_publication: dict[str, set[str]],
) -> None:
    members = [
        publication
        for category in result.technology_categories
        for publication in category.publication_numbers
    ]
    if set(members) != expected or len(members) != len(set(members)):
        raise CompanyTechnologyClassificationError(
            "lightweight categories must cover every eligible publication exactly once"
        )
    for category in result.technology_categories:
        cited = set(category.evidence_ids)
        allowed = set().union(
            *(evidence_by_publication[publication] for publication in category.publication_numbers)
        )
        if not cited <= allowed:
            raise CompanyTechnologyClassificationError(
                f"lightweight category cites unknown evidence: {category.category_id}"
            )
        if any(
            not (cited & evidence_by_publication[publication])
            for publication in category.publication_numbers
        ):
            raise CompanyTechnologyClassificationError(
                f"lightweight category leaves a patent uncited: {category.category_id}"
            )


__all__ = [
    "COMPANY_CLASSIFIER_NAME",
    "COMPANY_CATEGORY_CONSOLIDATOR_NAME",
    "LIGHTWEIGHT_COMPANY_CLASSIFIER_NAME",
    "CompanyTechnologyClassificationError",
    "CompanyTechnologyClassificationService",
    "validate_company_technology_classification",
]
