from __future__ import annotations

import unittest
from datetime import date

from pydantic import ValidationError

from landscape.schemas import (
    CompanyAssignment,
    CompanyTechnologyCategory,
    CompanyTechnologyProfile,
    CrossCompanyTrend,
    CrossCompanyTrendAnalysis,
    LandscapeCoverageAudit,
    NormalizedCompany,
    TrendTimeBasis,
)
from tests.landscape_agent_fixture_loader import load_json, load_jsonl


def category(
    category_id: str = "TC-A-01",
    publication_numbers: list[str] | None = None,
    evidence_ids: list[str] | None = None,
) -> CompanyTechnologyCategory:
    return CompanyTechnologyCategory(
        category_id=category_id,
        name="冷板换热",
        summary="微通道增加冷板换热面积。",
        keywords=["冷板", "微通道"],
        publication_numbers=publication_numbers or ["CN1A"],
        evidence_ids=evidence_ids or ["EV-1"],
    )


def trend(trend_id: str = "TR-01") -> CrossCompanyTrend:
    return CrossCompanyTrend(
        trend_id=trend_id,
        name="液冷控制相关公开增加",
        summary="两个季度均有不同公司的公开文本支持。",
        direction="GROWING",
        company_ids=["CO-A", "CO-B"],
        publication_numbers=["CN1A", "US2A1"],
        evidence_ids=["EV-1", "EV-2"],
        time_basis=TrendTimeBasis(
            start=date(2026, 1, 1),
            end=date(2026, 6, 30),
            bucket="QUARTER",
        ),
    )


class LandscapeCompanyTrendSchemaTests(unittest.TestCase):
    def test_trend_direction_contract_supports_observation_and_change_labels(self) -> None:
        schema = CrossCompanyTrend.model_json_schema()
        directions = set(
            schema["properties"]["direction"]["enum"]
        )
        self.assertEqual(
            directions,
            {
                "EMERGING",
                "GROWING",
                "DECLINING",
                "SHIFTING",
                "ACCELERATING",
                "STABLE",
                "UNCERTAIN",
            },
        )

    def test_committed_company_and_assignment_fixtures_validate(self) -> None:
        registry = load_json("alias_registry.json")
        companies = [
            NormalizedCompany.model_validate(value)
            for value in registry["companies"]
        ]
        assignments = [
            CompanyAssignment.model_validate(value)
            for value in registry["assignments"]
        ]
        self.assertEqual(len(assignments), 7)
        self.assertEqual(companies[-1].company_id, "UNKNOWN")
        self.assertEqual(assignments[-1].status, "UNKNOWN")

    def test_committed_agent_output_fixtures_validate(self) -> None:
        profiles = [
            CompanyTechnologyProfile.model_validate(value["output"])
            for value in load_jsonl("company_profile_outputs.jsonl")
        ]
        trend_output = load_jsonl("trend_outputs.jsonl")[0]["output"]
        cross_company = CrossCompanyTrendAnalysis.model_validate(trend_output)
        self.assertEqual(len(profiles), 5)
        self.assertEqual(cross_company.trends, [])

    def test_models_forbid_unknown_fields(self) -> None:
        with self.assertRaises(ValidationError):
            NormalizedCompany(
                company_id="CO-A",
                canonical_name="A",
                aliases=[],
                invented_field=True,
            )

    def test_company_aliases_are_trimmed_and_deduplicated(self) -> None:
        company = NormalizedCompany(
            company_id="CO-A",
            canonical_name="A",
            aliases=[" A Limited ", "a limited", "", "A公司"],
        )
        self.assertEqual(company.aliases, ["A Limited", "A公司"])

    def test_unknown_company_identity_is_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValidationError, "UNKNOWN company"):
            NormalizedCompany(
                company_id="UNKNOWN",
                canonical_name="Unknown Assignee",
                aliases=["Unknown"],
            )
        with self.assertRaisesRegex(ValidationError, "only the UNKNOWN"):
            NormalizedCompany(
                company_id="CO-A",
                canonical_name="UNKNOWN",
                aliases=[],
            )

    def test_confirmed_assignment_requires_observed_and_matched_names(self) -> None:
        with self.assertRaisesRegex(ValidationError, "requires observed_assignee"):
            CompanyAssignment(
                publication_number="CN1A",
                primary_company_id="CO-A",
                observed_assignee="A公司",
                matched_alias=None,
                status="CONFIRMED_ALIAS",
            )
        with self.assertRaisesRegex(ValidationError, "cannot target UNKNOWN"):
            CompanyAssignment(
                publication_number="CN1A",
                primary_company_id="UNKNOWN",
                observed_assignee="A公司",
                matched_alias="A公司",
                status="CONFIRMED_ALIAS",
            )

    def test_unknown_and_review_assignments_remain_in_unknown_bucket(self) -> None:
        with self.assertRaisesRegex(ValidationError, "must target UNKNOWN"):
            CompanyAssignment(
                publication_number="CN1A",
                primary_company_id="CO-A",
                observed_assignee="Ambiguous Holdings",
                status="REVIEW_REQUIRED",
            )
        with self.assertRaisesRegex(ValidationError, "cannot include matched_alias"):
            CompanyAssignment(
                publication_number="CN1A",
                primary_company_id="UNKNOWN",
                observed_assignee="Unverified Company",
                matched_alias="Company",
                status="UNKNOWN",
            )

    def test_category_rejects_duplicate_members_and_evidence(self) -> None:
        with self.assertRaisesRegex(ValidationError, "publication numbers"):
            category(publication_numbers=["CN1A", "CN1A"])
        with self.assertRaisesRegex(ValidationError, "evidence IDs"):
            category(evidence_ids=["EV-1", "EV-1"])

    def test_profile_rejects_duplicate_ids_and_cross_category_membership(self) -> None:
        with self.assertRaisesRegex(ValidationError, "category IDs"):
            CompanyTechnologyProfile(
                overall_summary="公司技术总结。",
                technology_directions=["液冷"],
                technology_categories=[
                    category("TC-A-01", ["CN1A"], ["EV-1"]),
                    category("TC-A-01", ["CN2A"], ["EV-2"]),
                ],
            )
        with self.assertRaisesRegex(ValidationError, "only one"):
            CompanyTechnologyProfile(
                overall_summary="公司技术总结。",
                technology_directions=["液冷"],
                technology_categories=[
                    category("TC-A-01", ["CN1A"], ["EV-1"]),
                    category("TC-A-02", ["CN1A"], ["EV-1"]),
                ],
            )

    def test_trend_time_basis_and_reference_sets_are_strict(self) -> None:
        with self.assertRaisesRegex(ValidationError, "end must be"):
            TrendTimeBasis(
                start=date(2026, 7, 1),
                end=date(2026, 6, 30),
                bucket="QUARTER",
            )
        value = trend().model_dump(mode="json")
        value["company_ids"] = ["CO-A", "CO-A"]
        with self.assertRaisesRegex(ValidationError, "company IDs"):
            CrossCompanyTrend.model_validate(value)

    def test_cross_company_analysis_rejects_duplicate_trend_ids(self) -> None:
        with self.assertRaisesRegex(ValidationError, "trend IDs"):
            CrossCompanyTrendAnalysis(
                overall_summary="多家公司聚焦液冷控制。",
                trends=[trend("TR-01"), trend("TR-01")],
            )

    def test_pass_audit_requires_full_clean_coverage(self) -> None:
        valid = LandscapeCoverageAudit(
            decision="PASS",
            coverage_ratio=1,
        )
        self.assertEqual(valid.decision, "PASS")
        with self.assertRaisesRegex(ValidationError, "PASS requires"):
            LandscapeCoverageAudit(
                decision="PASS",
                coverage_ratio=0.5,
                missing_publications=["CN2A"],
            )

    def test_repair_audit_requires_explicit_targets(self) -> None:
        with self.assertRaisesRegex(ValidationError, "repair target"):
            LandscapeCoverageAudit(
                decision="REPAIR",
                coverage_ratio=0.5,
                missing_publications=["CN2A"],
            )
        audit = LandscapeCoverageAudit(
            decision="REPAIR",
            coverage_ratio=0.5,
            missing_publications=["CN2A"],
            repair_targets=["ANALYZE:CN2A"],
        )
        self.assertEqual(audit.repair_targets, ["ANALYZE:CN2A"])


if __name__ == "__main__":
    unittest.main()
