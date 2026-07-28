from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from landscape.company_profiles import (
    COMPANY_PROFILE_AGENT_NAME,
    CompanyTechnologyProfileError,
    CompanyTechnologyProfileService,
    validate_company_technology_profile,
)
from landscape.schemas import (
    CompanyTechnologyCategory,
    CompanyTechnologyClassification,
    CompanyTechnologyProfile,
    CompanyTechnologyProfileNarrative,
)
from tests.test_landscape_company_classification import company_batch


class StubProfileModel:
    def __init__(self, output=None):
        self.output = output
        self.calls = []

    async def complete(self, agent_name, *, system_prompt, input_payload):
        self.calls.append((agent_name, system_prompt, input_payload))
        return SimpleNamespace(output=self.output)


def classification() -> CompanyTechnologyClassification:
    return CompanyTechnologyClassification(
        technology_categories=[
            CompanyTechnologyCategory(
                category_id="TC-HUAWEI-01",
                name="微通道换热",
                summary="微通道冷板提高换热效率。",
                keywords=["微通道", "冷板"],
                publication_numbers=["CN1A"],
                evidence_ids=["EV-CN1A"],
            ),
            CompanyTechnologyCategory(
                category_id="TC-HUAWEI-02",
                name="冷却控制",
                summary="依据负载调整冷却参数。",
                keywords=["控制"],
                publication_numbers=["US2A1"],
                evidence_ids=["EV-US2A1"],
            ),
        ]
    )


class LandscapeCompanyProfileTests(unittest.TestCase):
    def test_single_patent_profile_is_deterministic_without_model(self) -> None:
        model = StubProfileModel()
        service = CompanyTechnologyProfileService(model)
        one_classification = CompanyTechnologyClassification(
            technology_categories=[classification().technology_categories[0]]
        )

        profile = asyncio.run(
            service.build(company_batch(["CN1A"]), one_classification)
        )

        self.assertEqual(model.calls, [])
        self.assertEqual(profile.overall_summary, "微通道冷板提高换热效率。")
        self.assertEqual(profile.technology_directions, ["微通道换热"])
        self.assertEqual(
            profile.technology_categories,
            one_classification.technology_categories,
        )

    def test_model_only_generates_narrative_and_program_preserves_categories(self) -> None:
        narrative = CompanyTechnologyProfileNarrative(
            overall_summary="该公司同时布局换热结构与冷却控制。",
            technology_directions=["换热结构", "控制策略"],
            limitations=["仅基于本时间窗公开专利。"],
        )
        model = StubProfileModel(narrative)
        service = CompanyTechnologyProfileService(model)
        categories = classification()

        profile = asyncio.run(
            service.build(
                company_batch(["CN1A", "US2A1"]),
                categories,
            )
        )

        self.assertEqual(profile.technology_categories, categories.technology_categories)
        agent_name, _prompt, payload = model.calls[0]
        self.assertEqual(agent_name, COMPANY_PROFILE_AGENT_NAME)
        self.assertEqual(payload["company_name"], "Huawei")
        serialized = str(payload)
        for forbidden in (
            "company_id",
            "category_id",
            "publication_number",
            "evidence_id",
            "patent_count",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_profile_cannot_rewrite_validated_category_membership(self) -> None:
        batch = company_batch(["CN1A", "US2A1"])
        categories = classification()
        altered = CompanyTechnologyProfile(
            overall_summary="错误结果。",
            technology_directions=["错误方向"],
            technology_categories=[
                categories.technology_categories[0].model_copy(
                    update={"summary": "模型篡改后的总结。"}
                ),
                categories.technology_categories[1],
            ],
        )

        with self.assertRaisesRegex(
            CompanyTechnologyProfileError, "preserve validated categories"
        ):
            validate_company_technology_profile(batch, categories, altered)


if __name__ == "__main__":
    unittest.main()
