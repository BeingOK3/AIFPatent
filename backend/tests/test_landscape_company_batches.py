from __future__ import annotations

import unittest

from landscape.company_assignment import CompanyAssignmentResult
from landscape.company_batches import (
    CompanyAnalysisBatch,
    CompanyBatchValidationError,
    build_company_analysis_batches,
    validate_company_analysis_batches,
)
from landscape.schemas import (
    CompanyAssignment,
    LandscapeEvidenceRef,
    LandscapePatentAnalysis,
    NormalizedCompany,
)


def analysis(publication: str) -> LandscapePatentAnalysis:
    return LandscapePatentAnalysis(
        publication_number=publication,
        prior_art="现有技术",
        core_invention_points=["核心方案"],
        evidence_refs=[
            LandscapeEvidenceRef(
                evidence_id=f"EV-{publication}",
                supports=["prior_art", "core_invention_point"],
            )
        ],
    )


class LandscapeCompanyBatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.huawei = NormalizedCompany(
            company_id="CO-HUAWEI",
            canonical_name="Huawei",
            aliases=["华为"],
        )
        self.vertiv = NormalizedCompany(
            company_id="CO-VERTIV",
            canonical_name="Vertiv",
        )
        self.assignment_result = CompanyAssignmentResult(
            companies=(self.vertiv, self.huawei),
            assignments=(
                CompanyAssignment(
                    publication_number="US3A1",
                    primary_company_id="CO-VERTIV",
                    observed_assignee="Vertiv",
                    matched_alias="Vertiv",
                    status="CONFIRMED_ALIAS",
                ),
                CompanyAssignment(
                    publication_number="CN2A",
                    primary_company_id="CO-HUAWEI",
                    observed_assignee="华为",
                    matched_alias="华为",
                    status="CONFIRMED_ALIAS",
                ),
                CompanyAssignment(
                    publication_number="CN1A",
                    primary_company_id="CO-HUAWEI",
                    observed_assignee="Huawei",
                    matched_alias="Huawei",
                    status="CONFIRMED_ALIAS",
                ),
            ),
        )

    def test_batches_are_order_independent_and_cover_a_exactly(self) -> None:
        first = build_company_analysis_batches(
            self.assignment_result,
            {
                "US3A1": analysis("US3A1"),
                "CN2A": analysis("CN2A"),
                "CN1A": analysis("CN1A"),
            },
        )
        second = build_company_analysis_batches(
            CompanyAssignmentResult(
                companies=tuple(reversed(self.assignment_result.companies)),
                assignments=tuple(reversed(self.assignment_result.assignments)),
            ),
            {
                "CN1A": analysis("CN1A"),
                "CN2A": analysis("CN2A"),
                "US3A1": analysis("US3A1"),
            },
        )

        self.assertEqual(first, second)
        self.assertEqual(
            [(batch.company_id, batch.publication_numbers) for batch in first],
            [
                ("CO-HUAWEI", ("CN1A", "CN2A")),
                ("CO-VERTIV", ("US3A1",)),
            ],
        )

    def test_fetch_or_analysis_gaps_do_not_create_empty_company_batches(self) -> None:
        batches = build_company_analysis_batches(
            self.assignment_result,
            {"US3A1": analysis("US3A1")},
        )
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0].company_id, "CO-VERTIV")

    def test_unknown_analysis_and_wrong_batch_membership_fail_closed(self) -> None:
        with self.assertRaisesRegex(
            CompanyBatchValidationError, "outside company assignment"
        ):
            build_company_analysis_batches(
                self.assignment_result,
                {"EP9A1": analysis("EP9A1")},
            )

        valid = build_company_analysis_batches(
            self.assignment_result,
            {"CN1A": analysis("CN1A")},
        )[0]
        wrong = CompanyAnalysisBatch(
            company=self.vertiv,
            items=valid.items,
        )
        with self.assertRaisesRegex(
            CompanyBatchValidationError, "wrong company"
        ):
            validate_company_analysis_batches(
                [wrong],
                expected_publications={"CN1A"},
            )


if __name__ == "__main__":
    unittest.main()
