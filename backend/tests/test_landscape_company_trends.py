from __future__ import annotations

import asyncio
import unittest
from datetime import date
from types import SimpleNamespace

from landscape.company_trends import (
    CROSS_COMPANY_TREND_AGENT_NAME,
    CrossCompanyTrendService,
    CrossCompanyTrendValidationError,
)
from landscape.schemas import (
    CompanyTechnologyCategory,
    CompanyTechnologyProfile,
    CrossCompanyTrendProposal,
    CrossCompanyTrendProposalAnalysis,
    LandscapeEvidenceRef,
    LandscapePatentAnalysis,
    TrendTimeBasis,
)


class StubTrendModel:
    def __init__(self, output=None):
        self.output = output
        self.calls = []

    async def complete(self, agent_name, *, system_prompt, input_payload):
        self.calls.append((agent_name, system_prompt, input_payload))
        return SimpleNamespace(output=self.output)


def patent_analysis(publication: str) -> LandscapePatentAnalysis:
    return LandscapePatentAnalysis(
        publication_number=publication,
        prior_art="现有冷却方案。",
        core_invention_points=["改进冷却结构。"],
        evidence_refs=[
            LandscapeEvidenceRef(
                evidence_id=f"EV-{publication}",
                supports=["prior_art", "core_invention_point"],
            )
        ],
    )


def profile(
    company_token: str,
    publications: list[str],
) -> CompanyTechnologyProfile:
    return CompanyTechnologyProfile(
        overall_summary=f"{company_token} 聚焦液冷。",
        technology_directions=["液冷"],
        technology_categories=[
            CompanyTechnologyCategory(
                category_id=f"TC-{company_token}-01",
                name="液冷",
                summary="液冷技术方案。",
                publication_numbers=publications,
                evidence_ids=[
                    f"EV-{publication}" for publication in publications
                ],
            )
        ],
    )


class LandscapeCompanyTrendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profiles = {
            "CO-A": profile("A", ["CN1A", "CN2A"]),
            "CO-B": profile("B", ["US3A1"]),
        }
        self.analyses = {
            publication: patent_analysis(publication)
            for publication in ("CN1A", "CN2A", "US3A1")
        }
        self.dates = {
            "CN1A": date(2026, 1, 10),
            "CN2A": date(2026, 4, 10),
            "US3A1": date(2026, 5, 10),
        }
        self.time_basis = TrendTimeBasis(
            start=date(2026, 1, 1),
            end=date(2026, 6, 30),
            bucket="QUARTER",
        )

    def test_program_attaches_stable_ids_and_time_basis(self) -> None:
        proposal = CrossCompanyTrendProposalAnalysis(
            overall_summary="两家公司均布局液冷。",
            common_directions=["液冷"],
            trends=[
                CrossCompanyTrendProposal(
                    name="液冷布局扩展",
                    summary="两个季度有三件公开文本支持。",
                    direction="GROWING",
                    company_ids=["CO-B", "CO-A"],
                    publication_numbers=["US3A1", "CN2A", "CN1A"],
                    evidence_ids=[
                        "EV-US3A1",
                        "EV-CN2A",
                        "EV-CN1A",
                    ],
                )
            ],
        )
        model = StubTrendModel(proposal)
        service = CrossCompanyTrendService(model)

        result = asyncio.run(
            service.analyze(
                profiles=self.profiles,
                analyses=self.analyses,
                publication_dates=self.dates,
                time_basis=self.time_basis,
            )
        )

        trend = result.trends[0]
        self.assertEqual(trend.trend_id, "TR-01")
        self.assertEqual(trend.company_ids, ["CO-A", "CO-B"])
        self.assertEqual(
            trend.publication_numbers,
            ["CN1A", "CN2A", "US3A1"],
        )
        self.assertEqual(trend.time_basis, self.time_basis)
        agent_name, _prompt, payload = model.calls[0]
        self.assertEqual(agent_name, CROSS_COMPANY_TREND_AGENT_NAME)
        self.assertIn("time_bucket", str(payload))
        self.assertNotIn("publication_date", str(payload))
        self.assertNotIn("trend_id", str(payload))

    def test_directional_claim_requires_patent_and_bucket_thresholds(self) -> None:
        proposal = CrossCompanyTrendProposalAnalysis(
            overall_summary="观察到共同方向。",
            trends=[
                CrossCompanyTrendProposal(
                    name="不充分的增长结论",
                    summary="证据都在同一季度。",
                    direction="GROWING",
                    company_ids=["CO-A", "CO-B"],
                    publication_numbers=["CN2A", "US3A1"],
                    evidence_ids=["EV-CN2A", "EV-US3A1"],
                )
            ],
        )
        service = CrossCompanyTrendService(StubTrendModel(proposal))

        with self.assertRaisesRegex(
            CrossCompanyTrendValidationError, "lacks sufficient"
        ):
            asyncio.run(
                service.analyze(
                    profiles=self.profiles,
                    analyses=self.analyses,
                    publication_dates=self.dates,
                    time_basis=self.time_basis,
                )
            )

    def test_invented_evidence_and_wrong_company_chain_fail_closed(self) -> None:
        bad = CrossCompanyTrendProposalAnalysis(
            overall_summary="错误趋势。",
            trends=[
                CrossCompanyTrendProposal(
                    name="错误引用",
                    summary="公司与证据链均不合法。",
                    direction="UNCERTAIN",
                    company_ids=["CO-A", "CO-B"],
                    publication_numbers=["CN1A", "US3A1"],
                    evidence_ids=["EV-CN1A", "EV-INVENTED"],
                )
            ],
        )
        service = CrossCompanyTrendService(StubTrendModel(bad))
        with self.assertRaisesRegex(
            CrossCompanyTrendValidationError, "evidence"
        ):
            asyncio.run(
                service.analyze(
                    profiles=self.profiles,
                    analyses=self.analyses,
                    publication_dates=self.dates,
                    time_basis=self.time_basis,
                )
            )

    def test_single_company_returns_limited_result_without_model(self) -> None:
        model = StubTrendModel()
        service = CrossCompanyTrendService(model)
        result = asyncio.run(
            service.analyze(
                profiles={"CO-A": self.profiles["CO-A"]},
                analyses={
                    publication: self.analyses[publication]
                    for publication in ("CN1A", "CN2A")
                },
                publication_dates={
                    publication: self.dates[publication]
                    for publication in ("CN1A", "CN2A")
                },
                time_basis=self.time_basis,
            )
        )
        self.assertEqual(model.calls, [])
        self.assertEqual(result.trends, [])
        self.assertTrue(result.limitations)


if __name__ == "__main__":
    unittest.main()
