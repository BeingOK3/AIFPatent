from __future__ import annotations

import unittest

from landscape.company_assignment import CompanyAssignmentResult
from landscape.coverage_audit import audit_company_trend_coverage
from landscape.schemas import (
    CompanyAssignment,
    CompanyTechnologyCategory,
    CompanyTechnologyProfile,
    NormalizedCompany,
)
from tests.test_landscape_company_trends import patent_analysis, profile


class LandscapeCoverageAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.eligible = ["CN1A", "CN2A", "US3A1"]
        self.assignments = CompanyAssignmentResult(
            companies=(
                NormalizedCompany(
                    company_id="CO-A",
                    canonical_name="Company A",
                ),
                NormalizedCompany(
                    company_id="CO-B",
                    canonical_name="Company B",
                ),
            ),
            assignments=(
                CompanyAssignment(
                    publication_number="CN1A",
                    primary_company_id="CO-A",
                    observed_assignee="Company A",
                    matched_alias="Company A",
                    status="CONFIRMED_ALIAS",
                ),
                CompanyAssignment(
                    publication_number="CN2A",
                    primary_company_id="CO-A",
                    observed_assignee="Company A",
                    matched_alias="Company A",
                    status="CONFIRMED_ALIAS",
                ),
                CompanyAssignment(
                    publication_number="US3A1",
                    primary_company_id="CO-B",
                    observed_assignee="Company B",
                    matched_alias="Company B",
                    status="CONFIRMED_ALIAS",
                ),
            ),
        )
        self.analyses = {
            publication: patent_analysis(publication)
            for publication in self.eligible
        }
        self.profiles = {
            "CO-A": profile("A", ["CN1A", "CN2A"]),
            "CO-B": profile("B", ["US3A1"]),
        }
        self.evidence = {
            publication: {f"EV-{publication}"}
            for publication in self.eligible
        }

    def audit(self, **updates):
        values = {
            "eligible_publications": self.eligible,
            "assignment_result": self.assignments,
            "fetched_publications": set(self.eligible),
            "analyses": self.analyses,
            "profiles": self.profiles,
            "trends": None,
            "valid_evidence_ids_by_publication": self.evidence,
        }
        values.update(updates)
        return audit_company_trend_coverage(**values)

    def test_full_u_f_a_t_partition_passes(self) -> None:
        audit = self.audit()
        self.assertEqual(audit.decision, "PASS")
        self.assertEqual(audit.coverage_ratio, 1)
        self.assertEqual(audit.missing_publications, [])

    def test_fetch_analysis_and_classification_gaps_route_to_bounded_repair(self) -> None:
        analyses = {
            publication: self.analyses[publication]
            for publication in ("CN1A", "CN2A")
        }
        profiles = {"CO-A": self.profiles["CO-A"]}
        evidence = {
            publication: self.evidence[publication]
            for publication in analyses
        }
        audit = self.audit(
            analyses=analyses,
            profiles=profiles,
            valid_evidence_ids_by_publication=evidence,
        )
        self.assertEqual(audit.decision, "REPAIR")
        self.assertEqual(audit.coverage_ratio, 0.666667)
        self.assertEqual(audit.missing_publications, ["US3A1"])
        self.assertEqual(audit.repair_targets, ["ANALYZE:US3A1"])

        limited = self.audit(
            analyses=analyses,
            profiles=profiles,
            valid_evidence_ids_by_publication=evidence,
            repair_round=1,
            max_repair_rounds=1,
        )
        self.assertEqual(limited.decision, "LIMITED")
        self.assertEqual(limited.repair_targets, [])

    def test_invented_assignment_or_broken_set_order_fails(self) -> None:
        invented = CompanyAssignmentResult(
            companies=self.assignments.companies,
            assignments=(
                *self.assignments.assignments,
                CompanyAssignment(
                    publication_number="EP9A1",
                    primary_company_id="CO-A",
                    observed_assignee="Company A",
                    matched_alias="Company A",
                    status="CONFIRMED_ALIAS",
                ),
            ),
        )
        failed = self.audit(assignment_result=invented)
        self.assertEqual(failed.decision, "FAIL")
        self.assertEqual(failed.invented_publications, ["EP9A1"])

        classified_without_analysis = self.audit(
            analyses={"CN1A": self.analyses["CN1A"]},
            valid_evidence_ids_by_publication={
                "CN1A": self.evidence["CN1A"]
            },
        )
        self.assertEqual(classified_without_analysis.decision, "FAIL")
        self.assertTrue(
            any("T ⊆ A ⊆ F ⊆ U" in item for item in classified_without_analysis.limitations)
        )

    def test_duplicate_wrong_company_and_invalid_evidence_are_audited(self) -> None:
        wrong_profile = CompanyTechnologyProfile(
            overall_summary="错误归类。",
            technology_directions=["错误"],
            technology_categories=[
                CompanyTechnologyCategory(
                    category_id="TC-B-01",
                    name="错误",
                    summary="CN1A 被放入 B。",
                    publication_numbers=["CN1A", "US3A1"],
                    evidence_ids=["EV-CN1A", "EV-INVENTED"],
                )
            ],
        )
        audit = self.audit(
            profiles={
                "CO-A": self.profiles["CO-A"],
                "CO-B": wrong_profile,
            }
        )
        self.assertEqual(audit.decision, "REPAIR")
        self.assertEqual(audit.duplicate_memberships, ["CN1A"])
        self.assertEqual(audit.invalid_evidence_refs, ["EV-INVENTED"])
        self.assertIn("CLASSIFY:CN1A", audit.repair_targets)
        self.assertIn("PROFILE:CO-B", audit.repair_targets)


if __name__ == "__main__":
    unittest.main()
