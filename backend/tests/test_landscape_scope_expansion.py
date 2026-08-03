from __future__ import annotations

import asyncio
import os
import unittest
from types import SimpleNamespace

from pydantic import ValidationError

from idea.config import load_config
from idea.model_client import RuntimeModelConfig, StructuredModelClient, runtime_model_config

from landscape.scope import (
    CandidateSource,
    CandidateStatus,
    CompanyNameRelation,
    NameLanguage,
    TechnologyTermRelation,
)
from landscape.scope_expansion import (
    COMPANY_SCOPE_EXPANDER,
    TECHNOLOGY_SCOPE_EXPANDER,
    CompanyExpansionOutput,
    ProposedCompanyName,
    ProposedTechnologyTerm,
    ScopeExpansionError,
    ScopeExpansionService,
    TechnologyExpansionOutput,
    fallback_company_scope,
    fallback_technology_terms,
)
from landscape.scope_repository import CompanyProfileMemory
from tests.test_landscape_scope_repository import reviewed_fixture


class StubModel:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    async def complete(self, agent_name, *, system_prompt, input_payload):
        self.calls.append((agent_name, system_prompt, input_payload))
        return SimpleNamespace(output=self.outputs.pop(0))


def company_output(*candidates, input_name="华为"):
    return CompanyExpansionOutput(input_name=input_name, candidates=candidates)


def technology_output(*candidates, original_input="无线通信"):
    return TechnologyExpansionOutput(
        original_input=original_input,
        semantic_breadth="BROAD",
        candidates=candidates,
    )


class LandscapeScopeExpansionTests(unittest.TestCase):
    def test_company_expansion_merges_history_and_drops_repeated_exclusion(self) -> None:
        historical = reviewed_fixture().companies[0]
        memory = CompanyProfileMemory(profile_version=4, company=historical)
        repeated_excluded = ProposedCompanyName(
            text=historical.names[1].text,
            language=NameLanguage.EN,
            relation_type=CompanyNameRelation.TRANSLATION,
            rationale="重复项",
        )
        new_member = ProposedCompanyName(
            text="华为终端有限公司",
            language=NameLanguage.ZH,
            relation_type=CompanyNameRelation.SUBSIDIARY,
            rationale="可能的集团成员，需用户确认",
        )
        model = StubModel([company_output(repeated_excluded, new_member)])

        result = asyncio.run(
            ScopeExpansionService(model).expand_company("华为", memory=memory)
        )

        self.assertEqual(model.calls[0][0], COMPANY_SCOPE_EXPANDER)
        self.assertEqual(len(result.names), len(historical.names) + 1)
        self.assertEqual(result.names[1].status, CandidateStatus.EXCLUDED)
        self.assertEqual(result.names[-1].status, CandidateStatus.PROPOSED)
        self.assertEqual(result.names[-1].source, CandidateSource.MODEL_SUGGESTED)
        self.assertEqual(result.names[-1].relation_type, CompanyNameRelation.SUBSIDIARY)

    def test_new_company_keeps_user_input_active_and_model_names_proposed(self) -> None:
        english = ProposedCompanyName(
            text="Huawei Technologies Co., Ltd.",
            language=NameLanguage.EN,
            relation_type=CompanyNameRelation.LEGAL_NAME,
            rationale="英文法定名称候选",
        )
        result = asyncio.run(
            ScopeExpansionService(StubModel([company_output(english)])).expand_company("华为")
        )
        self.assertEqual(result.names[0].text, "华为")
        self.assertEqual(result.names[0].source, CandidateSource.USER_INPUT)
        self.assertEqual(result.names[0].status, CandidateStatus.ACTIVE)
        self.assertEqual(result.names[1].status, CandidateStatus.PROPOSED)

    def test_company_model_cannot_change_input_entity(self) -> None:
        service = ScopeExpansionService(StubModel([company_output(input_name="荣耀")]))
        with self.assertRaisesRegex(ScopeExpansionError, "changed"):
            asyncio.run(service.expand_company("华为"))

    def test_over_verbose_company_output_is_deterministically_bounded(self) -> None:
        proposals = tuple(
            ProposedCompanyName(
                text=f"测试关联公司{i}有限公司",
                language=NameLanguage.ZH,
                relation_type=CompanyNameRelation.GROUP_MEMBER,
                rationale="模型冗长输出的有界处理测试",
            )
            for i in range(100)
        )
        result = asyncio.run(
            ScopeExpansionService(StubModel([company_output(*proposals)])).expand_company("华为")
        )
        self.assertEqual(len(result.names), 41)
        self.assertEqual(result.names[-1].text, "测试关联公司39有限公司")

    def test_technology_expansion_is_bilingual_reviewable_and_deduplicated(self) -> None:
        output = technology_output(
            ProposedTechnologyTerm(
                text="无线通信",
                language=NameLanguage.ZH,
                relation_to_original=TechnologyTermRelation.ORIGINAL,
                rationale="原词重复",
            ),
            ProposedTechnologyTerm(
                text="wireless communication",
                language=NameLanguage.EN,
                relation_to_original=TechnologyTermRelation.TRANSLATION,
                rationale="英文对应词",
            ),
            ProposedTechnologyTerm(
                text="无线传输",
                language=NameLanguage.ZH,
                relation_to_original=TechnologyTermRelation.SYNONYM,
                rationale="常用同义表述",
            ),
        )
        model = StubModel([output])
        result = asyncio.run(ScopeExpansionService(model).expand_technology("无线通信"))
        self.assertEqual(model.calls[0][0], TECHNOLOGY_SCOPE_EXPANDER)
        self.assertEqual([item.text for item in result], ["无线通信", "wireless communication", "无线传输"])
        self.assertEqual(result[0].status, CandidateStatus.ACTIVE)
        self.assertTrue(all(item.status == CandidateStatus.PROPOSED for item in result[1:]))

    def test_technology_output_requires_zh_and_en_and_rejects_other(self) -> None:
        zh = ProposedTechnologyTerm(
            text="无线通信",
            language=NameLanguage.ZH,
            relation_to_original=TechnologyTermRelation.ORIGINAL,
            rationale="原始方向",
        )
        with self.assertRaisesRegex(ValidationError, "ZH and EN"):
            TechnologyExpansionOutput(
                original_input="无线通信",
                semantic_breadth="NARROW",
                candidates=(zh, zh.model_copy(update={"text": "无线传输"})),
            )
        with self.assertRaisesRegex(ValidationError, "ZH or EN"):
            ProposedTechnologyTerm(
                text="123",
                language=NameLanguage.OTHER,
                relation_to_original=TechnologyTermRelation.RELATED,
                rationale="非法语言",
            )

    def test_over_verbose_technology_output_is_deterministically_bounded(self) -> None:
        proposals = [
            ProposedTechnologyTerm(
                text="wireless communication",
                language=NameLanguage.EN,
                relation_to_original=TechnologyTermRelation.TRANSLATION,
                rationale="英文翻译候选",
            )
        ]
        proposals.extend(
            ProposedTechnologyTerm(
                text=f"无线通信组件{i}",
                language=NameLanguage.ZH,
                relation_to_original=TechnologyTermRelation.COMPONENT,
                rationale="模型冗长输出的有界处理测试",
            )
            for i in range(80)
        )
        result = asyncio.run(
            ScopeExpansionService(StubModel([technology_output(*proposals)])).expand_technology(
                "无线通信"
            )
        )
        self.assertEqual(len(result), 61)

    def test_model_cannot_mislabel_language_or_return_english_rationale(self) -> None:
        with self.assertRaisesRegex(ValidationError, "ZH candidate"):
            ProposedCompanyName(
                text="Huawei",
                language=NameLanguage.ZH,
                relation_type=CompanyNameRelation.ALIAS,
                rationale="中文理由",
            )
        with self.assertRaisesRegex(ValidationError, "Simplified Chinese"):
            ProposedCompanyName(
                text="Huawei",
                language=NameLanguage.EN,
                relation_type=CompanyNameRelation.ALIAS,
                rationale="English rationale only",
            )

    def test_fallbacks_never_invent_unreviewed_names_or_terms(self) -> None:
        company = fallback_company_scope("华为")
        terms = fallback_technology_terms("无线通信")
        self.assertEqual([item.text for item in company.names], ["华为"])
        self.assertEqual([item.text for item in terms], ["无线通信"])
        self.assertEqual(company.names[0].status, CandidateStatus.ACTIVE)
        self.assertEqual(terms[0].status, CandidateStatus.ACTIVE)


@unittest.skipUnless(
    os.environ.get("AIFPATENT_RUN_SCOPE_LLM_INTEGRATION") == "1",
    "set AIFPATENT_RUN_SCOPE_LLM_INTEGRATION=1 to test the real model",
)
class LandscapeScopeExpansionModelIntegrationTests(unittest.TestCase):
    def test_company_and_technology_expansion_run_concurrently(self) -> None:
        async def scenario():
            service = ScopeExpansionService(StructuredModelClient(load_config().model))
            return await asyncio.gather(
                service.expand_company("华为"),
                service.expand_technology("数据中心液冷冷板微通道歧管"),
            )

        runtime = RuntimeModelConfig(
            base_url=os.environ["LLM_BASE_URL"],
            api_key=os.environ["LLM_API_KEY"],
            model=os.environ["LLM_MODEL"],
        )
        with runtime_model_config(runtime):
            company, terms = asyncio.run(scenario())

        company_languages = {item.language for item in company.names}
        term_languages = {item.language for item in terms}
        self.assertIn(NameLanguage.ZH, company_languages)
        self.assertIn(NameLanguage.EN, company_languages)
        self.assertIn(NameLanguage.ZH, term_languages)
        self.assertIn(NameLanguage.EN, term_languages)
        self.assertTrue(any(item.status == CandidateStatus.PROPOSED for item in company.names))
        self.assertTrue(any(item.status == CandidateStatus.PROPOSED for item in terms))


if __name__ == "__main__":
    unittest.main()
