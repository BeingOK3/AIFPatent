from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from landscape.company_batches import CompanyAnalysisBatch, CompanyAnalysisItem
from landscape.company_classification import (
    COMPANY_CLASSIFIER_NAME,
    CompanyTechnologyClassificationError,
    CompanyTechnologyClassificationService,
    validate_company_technology_classification,
)
from landscape.schemas import (
    CompanyAssignment,
    CompanyTechnologyCategory,
    CompanyTechnologyClassification,
    LandscapeEvidenceRef,
    LandscapePatentAnalysis,
    NormalizedCompany,
)


def patent_analysis(publication: str, evidence: str) -> LandscapePatentAnalysis:
    return LandscapePatentAnalysis(
        publication_number=publication,
        prior_art="传统冷却结构换热能力有限。",
        core_invention_points=[f"{publication} 使用微通道冷板。"],
        technical_problems_solved=["提高换热效率。"],
        beneficial_effects=["降低热阻。"],
        technical_keywords=["微通道", "冷板"],
        evidence_refs=[
            LandscapeEvidenceRef(
                evidence_id=evidence,
                supports=[
                    "prior_art",
                    "core_invention_point",
                    "technical_problem_solved",
                    "beneficial_effect",
                ],
            )
        ],
    )


def company_batch(publications: list[str]) -> CompanyAnalysisBatch:
    company = NormalizedCompany(
        company_id="CO-HUAWEI",
        canonical_name="Huawei",
        aliases=["华为"],
    )
    return CompanyAnalysisBatch(
        company=company,
        items=tuple(
            CompanyAnalysisItem(
                publication_number=publication,
                assignment=CompanyAssignment(
                    publication_number=publication,
                    primary_company_id=company.company_id,
                    observed_assignee="华为",
                    matched_alias="华为",
                    status="CONFIRMED_ALIAS",
                ),
                analysis=patent_analysis(
                    publication,
                    f"EV-{publication}",
                ),
            )
            for publication in publications
        ),
    )


class StubModel:
    def __init__(self, output=None):
        self.output = output
        self.calls = []

    async def complete(self, agent_name, *, system_prompt, input_payload):
        self.calls.append((agent_name, system_prompt, input_payload))
        return SimpleNamespace(output=self.output)


class LandscapeCompanyClassificationTests(unittest.TestCase):
    def test_single_patent_uses_deterministic_category_without_model(self) -> None:
        model = StubModel()
        service = CompanyTechnologyClassificationService(model)

        result = asyncio.run(service.classify(company_batch(["CN1A"])))

        self.assertEqual(model.calls, [])
        category = result.technology_categories[0]
        self.assertEqual(category.category_id, "TC-HUAWEI-01")
        self.assertEqual(category.publication_numbers, ["CN1A"])
        self.assertEqual(category.evidence_ids, ["EV-CN1A"])

    def test_multi_patent_model_output_is_scoped_validated_and_stably_identified(self) -> None:
        output = CompanyTechnologyClassification(
            technology_categories=[
                CompanyTechnologyCategory(
                    category_id="TC-MODEL-B",
                    name="控制策略",
                    summary="根据负载控制冷却。",
                    keywords=["控制"],
                    publication_numbers=["US2A1"],
                    evidence_ids=["EV-US2A1"],
                ),
                CompanyTechnologyCategory(
                    category_id="TC-MODEL-A",
                    name="换热结构",
                    summary="使用微通道增强换热。",
                    keywords=["微通道"],
                    publication_numbers=["CN1A"],
                    evidence_ids=["EV-CN1A"],
                ),
            ]
        )
        model = StubModel(output)
        service = CompanyTechnologyClassificationService(model)

        result = asyncio.run(
            service.classify(company_batch(["CN1A", "US2A1"]))
        )

        self.assertEqual(
            [category.category_id for category in result.technology_categories],
            ["TC-HUAWEI-01", "TC-HUAWEI-02"],
        )
        agent_name, _prompt, payload = model.calls[0]
        self.assertEqual(agent_name, COMPANY_CLASSIFIER_NAME)
        self.assertEqual(payload["company_name"], "Huawei")
        self.assertNotIn("company_id", payload)
        self.assertEqual(
            [patent["publication_number"] for patent in payload["patents"]],
            ["CN1A", "US2A1"],
        )

    def test_missing_invented_and_cross_patent_evidence_fail_closed(self) -> None:
        batch = company_batch(["CN1A", "US2A1"])
        invented = CompanyTechnologyClassification(
            technology_categories=[
                CompanyTechnologyCategory(
                    category_id="TC-X-01",
                    name="错误分类",
                    summary="包含批次外专利。",
                    publication_numbers=["CN1A", "EP9A1"],
                    evidence_ids=["EV-CN1A"],
                )
            ]
        )
        with self.assertRaisesRegex(
            CompanyTechnologyClassificationError, "cover the batch exactly"
        ):
            validate_company_technology_classification(batch, invented)

        wrong_evidence = CompanyTechnologyClassification(
            technology_categories=[
                CompanyTechnologyCategory(
                    category_id="TC-X-01",
                    name="换热",
                    summary="证据被错误归入另一件专利。",
                    publication_numbers=["CN1A"],
                    evidence_ids=["EV-US2A1"],
                ),
                CompanyTechnologyCategory(
                    category_id="TC-X-02",
                    name="控制",
                    summary="控制方向。",
                    publication_numbers=["US2A1"],
                    evidence_ids=["EV-US2A1"],
                ),
            ]
        )
        with self.assertRaisesRegex(
            CompanyTechnologyClassificationError, "evidence mismatch"
        ):
            validate_company_technology_classification(batch, wrong_evidence)


if __name__ == "__main__":
    unittest.main()
