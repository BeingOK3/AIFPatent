from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from landscape.company_batches import CompanyAnalysisBatch, CompanyAnalysisItem
from landscape.company_classification import (
    COMPANY_CATEGORY_CONSOLIDATOR_NAME,
    COMPANY_CLASSIFIER_NAME,
    CompanyTechnologyClassificationError,
    CompanyTechnologyClassificationService,
    validate_company_technology_classification,
)
from landscape.schemas import (
    CompanyAssignment,
    CompanyTechnologyCategory,
    CompanyTechnologyClassification,
    CompanyTechnologyClassificationDraft,
    LandscapeEvidenceRef,
    LandscapeDirectionEvidence,
    LandscapeDirectionFingerprint,
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


class BatchingStubModel:
    """Return one category for the first 8-item lightweight batch."""

    def __init__(self):
        self.calls = []

    async def complete(self, agent_name, *, system_prompt, input_payload):
        self.calls.append((agent_name, system_prompt, input_payload))
        patents = input_payload["patents"]
        return SimpleNamespace(
            output=CompanyTechnologyClassificationDraft(
                technology_categories=[
                    CompanyTechnologyCategory(
                        category_id="TC-MODEL-01",
                        name="批次一方向",
                        summary="第一批专利的技术方向。",
                        keywords=["批次一"],
                        publication_numbers=[
                            item["publication_number"] for item in patents
                        ],
                        evidence_ids=[
                            item["evidence"][0]["evidence_id"]
                            for item in patents
                        ],
                    )
                ]
            )
        )


class ConsolidatingBatchingStubModel:
    """Return distinct batch labels, then merge them through the new stage."""

    def __init__(self):
        self.calls = []

    async def complete(self, agent_name, *, system_prompt, input_payload):
        self.calls.append((agent_name, system_prompt, input_payload))
        if agent_name == COMPANY_CATEGORY_CONSOLIDATOR_NAME:
            source_categories = input_payload["source_categories"]
            first, *remaining = source_categories
            merged_publications = [
                *first["publication_numbers"],
                *remaining[0]["publication_numbers"],
            ]
            merged_evidence = [
                *first["evidence_ids"],
                *remaining[0]["evidence_ids"],
            ]
            return SimpleNamespace(
                output=CompanyTechnologyClassificationDraft(
                    technology_categories=[
                        CompanyTechnologyCategory(
                            category_id="TC-MODEL-MERGED",
                            name="合并方向",
                            summary="相近的首两个轻量方向。",
                            keywords=["合并"],
                            publication_numbers=merged_publications,
                            evidence_ids=merged_evidence,
                        ),
                        *[
                            CompanyTechnologyCategory(
                                category_id=f"TC-MODEL-{index:02d}",
                                name=category["name"],
                                summary=category["summary"],
                                keywords=category["keywords"],
                                publication_numbers=category["publication_numbers"],
                                evidence_ids=category["evidence_ids"],
                            )
                            for index, category in enumerate(remaining[1:], start=2)
                        ],
                    ]
                )
            )

        patents = input_payload["patents"]
        return SimpleNamespace(
            output=CompanyTechnologyClassificationDraft(
                technology_categories=[
                    CompanyTechnologyCategory(
                        category_id=f"TC-MODEL-{item['publication_number']}",
                        name=f"方向-{item['publication_number']}",
                        summary=f"{item['publication_number']} 的轻量技术方向。",
                        keywords=[item["publication_number"]],
                        publication_numbers=[item["publication_number"]],
                        evidence_ids=[item["evidence"][0]["evidence_id"]],
                    )
                    for item in patents
                ]
            )
        )


class InvalidConsolidatingBatchingStubModel(ConsolidatingBatchingStubModel):
    """Simulate a model that loses source memberships during compression."""

    async def complete(self, agent_name, *, system_prompt, input_payload):
        if agent_name != COMPANY_CATEGORY_CONSOLIDATOR_NAME:
            return await super().complete(
                agent_name,
                system_prompt=system_prompt,
                input_payload=input_payload,
            )
        self.calls.append((agent_name, system_prompt, input_payload))
        source = input_payload["source_categories"][0]
        return SimpleNamespace(
            output=CompanyTechnologyClassificationDraft(
                technology_categories=[
                    CompanyTechnologyCategory(
                        category_id="TC-MODEL-INVALID",
                        name="遗漏成员的错误合并",
                        summary="只错误保留了一个原始类别。",
                        keywords=["错误"],
                        publication_numbers=source["publication_numbers"],
                        evidence_ids=source["evidence_ids"],
                    )
                ]
            )
        )


class LandscapeCompanyClassificationTests(unittest.TestCase):
    def test_single_lightweight_fingerprint_uses_deterministic_category(self) -> None:
        service = CompanyTechnologyClassificationService(StubModel())
        fingerprint = LandscapeDirectionFingerprint(
            publication_number="CN1A",
            company_id="CO-HUAWEI",
            title="微通道冷板",
            publication_date="2026-06-01",
            source_kind="SEARCH_HIT",
            technical_keywords=["微通道", "冷板"],
            evidence=[
                LandscapeDirectionEvidence(
                    evidence_id="EV-DIR-CN1A",
                    section_type="SNIPPET",
                    text="微通道冷板提高换热效率。",
                    content_hash="a" * 64,
                )
            ],
        )
        result = asyncio.run(
            service.classify_fingerprints(
                company=NormalizedCompany(
                    company_id="CO-HUAWEI",
                    canonical_name="Huawei",
                    aliases=["华为"],
                ),
                fingerprints=[fingerprint],
            )
        )
        self.assertEqual(result.technology_categories[0].publication_numbers, ["CN1A"])
        self.assertEqual(result.technology_categories[0].category_id, "TC-HUAWEI-01")

    def test_single_patent_uses_deterministic_category_without_model(self) -> None:
        model = StubModel()
        service = CompanyTechnologyClassificationService(model)

        result = asyncio.run(service.classify(company_batch(["CN1A"])))

        self.assertEqual(model.calls, [])
        category = result.technology_categories[0]
        self.assertEqual(category.category_id, "TC-HUAWEI-01")
        self.assertEqual(category.publication_numbers, ["CN1A"])
        self.assertEqual(category.evidence_ids, ["EV-CN1A"])

    def test_lightweight_duplicate_model_ids_are_normalized_before_strict_validation(
        self,
    ) -> None:
        fingerprints = [
            LandscapeDirectionFingerprint(
                publication_number=publication,
                company_id="CO-HUAWEI",
                title=title,
                publication_date="2026-06-01",
                source_kind="SEARCH_HIT",
                technical_keywords=[keyword],
                evidence=[
                    LandscapeDirectionEvidence(
                        evidence_id=f"EV-DIR-{publication}",
                        section_type="SNIPPET",
                        text=f"{title}相关技术。",
                        content_hash="b" * 64,
                    )
                ],
            )
            for publication, title, keyword in (
                ("CN1A", "微通道冷板", "微通道"),
                ("US2A1", "负载控制", "控制"),
            )
        ]
        output = CompanyTechnologyClassificationDraft(
            technology_categories=[
                CompanyTechnologyCategory(
                    category_id="TC-DUP",
                    name="换热结构",
                    summary="微通道增强换热。",
                    keywords=["微通道"],
                    publication_numbers=["CN1A"],
                    evidence_ids=["EV-DIR-CN1A"],
                ),
                CompanyTechnologyCategory(
                    category_id="TC-DUP",
                    name="控制策略",
                    summary="根据负载控制。",
                    keywords=["控制"],
                    publication_numbers=["US2A1"],
                    evidence_ids=["EV-DIR-US2A1"],
                ),
            ]
        )
        model = StubModel(output)
        service = CompanyTechnologyClassificationService(model)

        result = asyncio.run(
            service.classify_fingerprints(
                company=NormalizedCompany(
                    company_id="CO-HUAWEI",
                    canonical_name="Huawei",
                    aliases=["华为"],
                ),
                fingerprints=fingerprints,
            )
        )

        self.assertEqual(
            [category.category_id for category in result.technology_categories],
            ["TC-HUAWEI-01", "TC-HUAWEI-02"],
        )
        self.assertEqual(
            model.calls[0][0], "landscape-company-lightweight-classifier"
        )

    def test_cross_batch_temporary_category_ids_are_renumbered_before_validation(
        self,
    ) -> None:
        fingerprints = [
            LandscapeDirectionFingerprint(
                publication_number=f"P{index:02d}",
                company_id="CO-HUAWEI",
                title=f"专利 {index}",
                publication_date="2026-06-01",
                source_kind="SEARCH_HIT",
                technical_keywords=["批次一" if index <= 8 else "批次二"],
                evidence=[
                    LandscapeDirectionEvidence(
                        evidence_id=f"EV-DIR-P{index:02d}",
                        section_type="SNIPPET",
                        text=f"专利 {index} 的技术摘要。",
                        content_hash="c" * 64,
                    )
                ],
            )
            for index in range(1, 10)
        ]
        model = BatchingStubModel()
        service = CompanyTechnologyClassificationService(model)

        result = asyncio.run(
            service.classify_fingerprints(
                company=NormalizedCompany(
                    company_id="CO-HUAWEI",
                    canonical_name="Huawei",
                    aliases=["华为"],
                ),
                fingerprints=fingerprints,
            )
        )

        self.assertEqual(len(model.calls), 1)
        self.assertEqual(
            [category.category_id for category in result.technology_categories],
            ["TC-HUAWEI-01", "TC-HUAWEI-02"],
        )
        self.assertEqual(
            {
                publication
                for category in result.technology_categories
                for publication in category.publication_numbers
            },
            {f"P{index:02d}" for index in range(1, 10)},
        )

    def test_oversized_cross_batch_categories_are_consolidated_before_validation(
        self,
    ) -> None:
        fingerprints = [
            LandscapeDirectionFingerprint(
                publication_number=f"P{index:02d}",
                company_id="CO-HUAWEI",
                title=f"专利 {index}",
                publication_date="2026-06-01",
                source_kind="SEARCH_HIT",
                technical_keywords=[f"方向{index}"],
                evidence=[
                    LandscapeDirectionEvidence(
                        evidence_id=f"EV-DIR-P{index:02d}",
                        section_type="SNIPPET",
                        text=f"专利 {index} 的技术摘要。",
                        content_hash="d" * 64,
                    )
                ],
            )
            for index in range(1, 22)
        ]
        model = ConsolidatingBatchingStubModel()
        service = CompanyTechnologyClassificationService(model)

        result = asyncio.run(
            service.classify_fingerprints(
                company=NormalizedCompany(
                    company_id="CO-HUAWEI",
                    canonical_name="Huawei",
                    aliases=["华为"],
                ),
                fingerprints=fingerprints,
            )
        )

        self.assertEqual(len(result.technology_categories), 20)
        self.assertIn(
            COMPANY_CATEGORY_CONSOLIDATOR_NAME,
            [call[0] for call in model.calls],
        )
        self.assertEqual(
            {
                publication
                for category in result.technology_categories
                for publication in category.publication_numbers
            },
            {f"P{index:02d}" for index in range(1, 22)},
        )

    def test_invalid_model_consolidation_falls_back_without_losing_patents(
        self,
    ) -> None:
        fingerprints = [
            LandscapeDirectionFingerprint(
                publication_number=f"P{index:02d}",
                company_id="CO-HUAWEI",
                title=f"专利 {index}",
                publication_date="2026-06-01",
                source_kind="SEARCH_HIT",
                technical_keywords=[f"方向{index}"],
                evidence=[
                    LandscapeDirectionEvidence(
                        evidence_id=f"EV-DIR-P{index:02d}",
                        section_type="SNIPPET",
                        text=f"专利 {index} 的技术摘要。",
                        content_hash="e" * 64,
                    )
                ],
            )
            for index in range(1, 22)
        ]
        result = asyncio.run(
            CompanyTechnologyClassificationService(
                InvalidConsolidatingBatchingStubModel()
            ).classify_fingerprints(
                company=NormalizedCompany(
                    company_id="CO-HUAWEI",
                    canonical_name="Huawei",
                    aliases=["华为"],
                ),
                fingerprints=fingerprints,
            )
        )

        self.assertEqual(len(result.technology_categories), 20)
        self.assertIn(
            "其他已识别技术方向",
            [category.name for category in result.technology_categories],
        )
        self.assertEqual(
            {
                publication
                for category in result.technology_categories
                for publication in category.publication_numbers
            },
            {f"P{index:02d}" for index in range(1, 22)},
        )

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

    def test_deep_classifier_duplicate_model_ids_are_normalized_before_validation(
        self,
    ) -> None:
        output = CompanyTechnologyClassificationDraft(
            technology_categories=[
                CompanyTechnologyCategory(
                    category_id="TC-SAME",
                    name="控制策略",
                    summary="根据负载控制冷却。",
                    keywords=["控制"],
                    publication_numbers=["US2A1"],
                    evidence_ids=["EV-US2A1"],
                ),
                CompanyTechnologyCategory(
                    category_id="TC-SAME",
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

        result = asyncio.run(service.classify(company_batch(["CN1A", "US2A1"])))

        self.assertEqual(
            [category.category_id for category in result.technology_categories],
            ["TC-HUAWEI-01", "TC-HUAWEI-02"],
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
