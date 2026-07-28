from __future__ import annotations

import unittest

from landscape.company_assignment import CompanyAssignmentResult
from landscape.repair import LandscapeRepairPlanError, build_repair_plan
from landscape.schemas import (
    CompanyAssignment,
    LandscapeCoverageAudit,
    NormalizedCompany,
)


class LandscapeRepairPlanTests(unittest.TestCase):
    @staticmethod
    def assignments() -> CompanyAssignmentResult:
        return CompanyAssignmentResult(
            companies=(
                NormalizedCompany(company_id="CO-A", canonical_name="A"),
                NormalizedCompany(company_id="CO-B", canonical_name="B"),
            ),
            assignments=(
                CompanyAssignment(
                    publication_number="CN1A",
                    primary_company_id="CO-A",
                    observed_assignee="A",
                    status="NORMALIZED_NAME",
                ),
                CompanyAssignment(
                    publication_number="US2A1",
                    primary_company_id="CO-B",
                    observed_assignee="B",
                    status="NORMALIZED_NAME",
                ),
            ),
        )

    def test_plan_derives_only_bounded_dependencies(self) -> None:
        audit = LandscapeCoverageAudit(
            decision="REPAIR",
            coverage_ratio=0.5,
            missing_publications=["CN1A"],
            repair_targets=[
                "FETCH:CN1A",
                "ANALYZE:US2A1",
                "CLASSIFY:US2A1",
                "PROFILE:CO-A",
                "TREND:TR-1",
            ],
        )

        plan = build_repair_plan(
            audit,
            eligible_publications=["CN1A", "US2A1"],
            assignment_result=self.assignments(),
        )

        self.assertEqual(plan.fetch_publications, ("CN1A",))
        self.assertEqual(plan.analyze_publications, ("CN1A", "US2A1"))
        self.assertEqual(plan.rebuild_company_ids, ("CO-A", "CO-B"))
        self.assertTrue(plan.rebuild_cross_company_trends)

    def test_plan_rejects_non_repair_audit_and_scope_escape(self) -> None:
        with self.assertRaisesRegex(LandscapeRepairPlanError, "requires REPAIR"):
            build_repair_plan(
                LandscapeCoverageAudit(decision="PASS", coverage_ratio=1),
                eligible_publications=["CN1A", "US2A1"],
                assignment_result=self.assignments(),
            )
        with self.assertRaisesRegex(LandscapeRepairPlanError, "outside eligible"):
            build_repair_plan(
                LandscapeCoverageAudit(
                    decision="REPAIR",
                    coverage_ratio=0.5,
                    missing_publications=["CN1A"],
                    repair_targets=["FETCH:CN999A"],
                ),
                eligible_publications=["CN1A", "US2A1"],
                assignment_result=self.assignments(),
            )

    def test_plan_rejects_unknown_company_and_target_shape(self) -> None:
        for target, message in (
            ("PROFILE:CO-MISSING", "unknown company"),
            ("SEARCH:CN1A", "unsupported"),
            ("FETCH", "malformed"),
        ):
            with self.subTest(target=target), self.assertRaisesRegex(
                LandscapeRepairPlanError, message
            ):
                build_repair_plan(
                    LandscapeCoverageAudit(
                        decision="REPAIR",
                        coverage_ratio=0.5,
                        missing_publications=["CN1A"],
                        repair_targets=[target],
                    ),
                    eligible_publications=["CN1A", "US2A1"],
                    assignment_result=self.assignments(),
                )


if __name__ == "__main__":
    unittest.main()
