from __future__ import annotations

import unittest
from datetime import date

from idea.merge import MergedHit, SourceRecord
from landscape.company_assignment import (
    CompanyAssignmentValidationError,
    assign_companies,
    validate_company_assignments,
)
from landscape.schemas import (
    AnalysisMode,
    AssigneeScope,
    CompanyAssignment,
    CompetitorInput,
    LandscapeScope,
    NormalizedCompany,
)


def hit(publication: str, assignee: str | None) -> MergedHit:
    return MergedHit(
        merge_key=f"publication:{publication}",
        publication_number=publication,
        assignee=assignee,
        found_by=["fixture"],
        query_ids=["Q-1"],
        sources=[],
    )


def scope(
    *,
    technology: str | None = None,
    competitors: list[CompetitorInput] | None = None,
) -> LandscapeScope:
    return LandscapeScope(
        technology_direction=technology,
        competitors=competitors or [],
        publication_start=date(2026, 1, 1),
        publication_end=date(2026, 6, 30),
    )


class LandscapeCompanyAssignmentTests(unittest.TestCase):
    def test_competitor_mode_assigns_base_fixture_names_and_unknown(self) -> None:
        value = scope(
            technology="数据中心液冷",
            competitors=[
                CompetitorInput(
                    name="Huawei",
                    aliases=[
                        "Huawei Technologies Co., Ltd.",
                        "华为技术有限公司",
                    ],
                ),
                CompetitorInput(
                    name="Vertiv",
                    aliases=["Vertiv Corporation", "维谛技术有限公司"],
                ),
                CompetitorInput(name="Meta", aliases=["Meta Platforms, Inc."]),
                CompetitorInput(
                    name="Metallurgy Systems",
                    aliases=["Metallurgy Systems Ltd."],
                ),
            ],
        )
        result = assign_companies(
            [
                hit("CN119000001A", "Huawei Technologies Co., Ltd."),
                hit("CN119000002A", "华为技术有限公司"),
                hit("US20260100001A1", "Vertiv Corporation"),
                hit("CN119000003A", "维谛技术有限公司"),
                hit("US20260100002A1", "Meta Platforms, Inc."),
                hit("EP4600001A1", "Metallurgy Systems Ltd."),
                hit("WO2026123456A1", None),
            ],
            value,
            user_confirmed_competitors=value.competitors,
        )

        by_publication = {
            assignment.publication_number: assignment
            for assignment in result.assignments
        }
        self.assertEqual(
            by_publication["US20260100002A1"].primary_company_id,
            "CO-META",
        )
        self.assertEqual(
            by_publication["EP4600001A1"].primary_company_id,
            "CO-METALLURGY",
        )
        self.assertEqual(
            by_publication["WO2026123456A1"].primary_company_id,
            "UNKNOWN",
        )
        self.assertEqual(
            {assignment.primary_company_id for assignment in result.assignments},
            {
                "CO-HUAWEI",
                "CO-VERTIV",
                "CO-META",
                "CO-METALLURGY",
                "UNKNOWN",
            },
        )

    def test_confirmed_alias_requires_exact_normalized_equality(self) -> None:
        value = scope(competitors=[CompetitorInput(name="Meta")])
        result = assign_companies(
            [hit("P1", "Metallurgy Systems Ltd.")],
            value,
            user_confirmed_competitors=value.competitors,
        )
        self.assertEqual(result.assignments[0].status, "UNKNOWN")
        self.assertEqual(result.assignments[0].primary_company_id, "UNKNOWN")

    def test_group_scope_assigns_legal_entities_with_auditable_status(self) -> None:
        value = scope(
            competitors=[
                CompetitorInput(
                    name="华为", assignee_scope=AssigneeScope.GROUP
                )
            ]
        )
        result = assign_companies(
            [
                hit("P1", "华为技术有限公司"),
                hit("P2", "华为终端有限公司"),
                hit("P3", "华为云计算技术有限公司"),
            ],
            value,
            user_confirmed_competitors=value.competitors,
        )

        self.assertEqual(
            len({assignment.primary_company_id for assignment in result.assignments}),
            1,
        )
        self.assertTrue(
            all(
                assignment.primary_company_id != "UNKNOWN"
                and assignment.status == "CONFIRMED_GROUP_SCOPE"
                and assignment.matched_alias == "华为"
                for assignment in result.assignments
            )
        )
        self.assertEqual(result.companies[0].assignee_scope, AssigneeScope.GROUP)

    def test_group_scope_ambiguous_match_is_review_required(self) -> None:
        value = scope(
            competitors=[
                CompetitorInput(name="华为", assignee_scope=AssigneeScope.GROUP),
                CompetitorInput(name="华为云", assignee_scope=AssigneeScope.GROUP),
            ]
        )
        result = assign_companies(
            [hit("P1", "华为云计算技术有限公司")],
            value,
            user_confirmed_competitors=value.competitors,
        )
        self.assertEqual(result.assignments[0].primary_company_id, "UNKNOWN")
        self.assertEqual(result.assignments[0].status, "REVIEW_REQUIRED")

    def test_validator_rejects_forged_group_scope_assignment(self) -> None:
        companies = [
            NormalizedCompany(
                company_id="CO-HUAWEI",
                canonical_name="华为",
                assignee_scope=AssigneeScope.ENTITY,
            )
        ]
        assignment = CompanyAssignment(
            publication_number="P1",
            primary_company_id="CO-HUAWEI",
            observed_assignee="华为终端有限公司",
            matched_alias="华为",
            status="CONFIRMED_GROUP_SCOPE",
        )
        with self.assertRaisesRegex(
            CompanyAssignmentValidationError, "group-scope assignment"
        ):
            validate_company_assignments(
                [hit("P1", "华为终端有限公司")], companies, [assignment]
            )

    def test_unconfirmed_composite_company_name_stays_unknown(self) -> None:
        value = scope(
            competitors=[
                CompetitorInput(name="Alpha"),
                CompetitorInput(name="Beta"),
            ]
        )
        result = assign_companies(
            [hit("P1", "Alpha Beta Holdings")],
            value,
            user_confirmed_competitors=value.competitors,
        )
        self.assertEqual(result.assignments[0].status, "UNKNOWN")
        self.assertEqual(result.assignments[0].primary_company_id, "UNKNOWN")

    def test_pure_technology_mode_only_groups_exact_normalized_names(self) -> None:
        result = assign_companies(
            [
                hit("P1", "Acme, Inc."),
                hit("P2", " ACME,   INC. "),
                hit("P3", "Acme Holdings"),
                hit("P4", "Acme"),
                hit("P5", None),
            ],
            scope(technology="液冷"),
        )
        assignment_by_publication = {
            assignment.publication_number: assignment
            for assignment in result.assignments
        }
        self.assertEqual(
            assignment_by_publication["P1"].primary_company_id,
            assignment_by_publication["P2"].primary_company_id,
        )
        self.assertNotEqual(
            assignment_by_publication["P1"].primary_company_id,
            assignment_by_publication["P3"].primary_company_id,
        )
        self.assertNotEqual(
            assignment_by_publication["P3"].primary_company_id,
            assignment_by_publication["P4"].primary_company_id,
        )
        self.assertEqual(assignment_by_publication["P1"].status, "NORMALIZED_NAME")
        self.assertEqual(assignment_by_publication["P5"].status, "UNKNOWN")
        self.assertTrue(
            assignment_by_publication["P1"].primary_company_id.startswith(
                "CO-RAW-"
            )
        )

    def test_output_and_ids_are_stable_when_hit_order_changes(self) -> None:
        hits = [
            hit("P2", "同名科技有限公司"),
            hit("P1", "ACME Inc."),
            hit("P3", None),
        ]
        value = scope(technology="液冷")
        forward = assign_companies(hits, value)
        reverse = assign_companies(reversed(hits), value)
        self.assertEqual(forward, reverse)

    def test_unstructured_joint_assignee_is_not_split_into_co_assignees(self) -> None:
        result = assign_companies(
            [hit("P1", "Acme Inc.; University Lab | Research Center")],
            scope(technology="液冷"),
        )
        self.assertEqual(result.assignments[0].co_assignees, [])
        self.assertEqual(len(result.assignments), 1)

    def test_conflicting_source_assignees_fail_closed(self) -> None:
        merged = hit("P1", "Huawei Technologies")
        merged = merged.model_copy(
            update={
                "sources": [
                    SourceRecord(
                        provider="one",
                        provider_rank=1,
                        query_id="Q-1",
                        url="https://one.test/P1",
                        raw={"assignee": "Huawei Technologies"},
                    ),
                    SourceRecord(
                        provider="two",
                        provider_rank=2,
                        query_id="Q-2",
                        url="https://two.test/P1",
                        raw={"assignee": "Unrelated Holdings"},
                    ),
                ]
            }
        )
        value = scope(competitors=[CompetitorInput(name="Huawei")])
        result = assign_companies(
            [merged],
            value,
            user_confirmed_competitors=value.competitors,
        )
        self.assertEqual(result.assignments[0].status, "REVIEW_REQUIRED")
        self.assertEqual(result.assignments[0].primary_company_id, "UNKNOWN")

    def test_registry_alias_collision_is_rejected_before_assignment(self) -> None:
        with self.assertRaisesRegex(
            CompanyAssignmentValidationError,
            "multiple companies",
        ):
            value = scope(
                competitors=[
                    CompetitorInput(name="Alpha", aliases=["Shared Alias"]),
                    CompetitorInput(name="Beta", aliases=[" shared   alias "]),
                ]
            )
            assign_companies(
                [hit("P1", "Shared Alias")],
                value,
                user_confirmed_competitors=value.competitors,
            )

    def test_confirmed_company_id_slug_collision_fails_closed(self) -> None:
        value = scope(
            competitors=[
                CompetitorInput(name="Alpha Holdings"),
                CompetitorInput(name="Alpha Research"),
            ]
        )
        with self.assertRaisesRegex(
            CompanyAssignmentValidationError,
            "company ID collision",
        ):
            assign_companies(
                [hit("P1", "Alpha Holdings")],
                value,
                user_confirmed_competitors=value.competitors,
            )
            assign_companies(
                [hit("P1", "Shared Alias")],
                value,
                user_confirmed_competitors=value.competitors,
            )

    def test_validator_rejects_matched_alias_owned_by_another_company(self) -> None:
        companies = [
            NormalizedCompany(
                company_id="CO-A",
                canonical_name="Alpha",
                aliases=["A Corp"],
            ),
            NormalizedCompany(
                company_id="CO-B",
                canonical_name="Beta",
                aliases=["B Corp"],
            ),
        ]
        assignment = CompanyAssignment(
            publication_number="P1",
            primary_company_id="CO-A",
            observed_assignee="B Corp",
            matched_alias="B Corp",
            status="CONFIRMED_ALIAS",
        )
        with self.assertRaisesRegex(
            CompanyAssignmentValidationError,
            "not registered",
        ):
            validate_company_assignments(
                [hit("P1", "B Corp")],
                companies,
                [assignment],
            )

    def test_validator_rejects_missing_duplicate_outside_and_bad_company_refs(self) -> None:
        hits = [hit("P1", "A"), hit("P2", "B")]
        companies = [NormalizedCompany(company_id="CO-A", canonical_name="A")]
        valid = CompanyAssignment(
            publication_number="P1",
            primary_company_id="CO-A",
            observed_assignee="A",
            status="NORMALIZED_NAME",
        )
        with self.assertRaisesRegex(
            CompanyAssignmentValidationError,
            "missing",
        ):
            validate_company_assignments(hits, companies, [valid])
        with self.assertRaisesRegex(
            CompanyAssignmentValidationError,
            "duplicate primary",
        ):
            validate_company_assignments(hits[:1], companies, [valid, valid])
        outside = valid.model_copy(update={"publication_number": "P3"})
        with self.assertRaisesRegex(
            CompanyAssignmentValidationError,
            "outside U",
        ):
            validate_company_assignments(hits[:1], companies, [outside])
        bad_ref = valid.model_copy(update={"primary_company_id": "CO-MISSING"})
        with self.assertRaisesRegex(
            CompanyAssignmentValidationError,
            "unknown companies",
        ):
            validate_company_assignments(hits[:1], companies, [bad_ref])

    def test_mode_is_derived_and_combined_mode_uses_confirmed_aliases(self) -> None:
        value = scope(
            technology="液冷",
            competitors=[CompetitorInput(name="Meta")],
        )
        self.assertEqual(value.mode, AnalysisMode.TECHNOLOGY_COMPETITOR)
        result = assign_companies(
            [hit("P1", "Unlisted Company")],
            value,
            user_confirmed_competitors=value.competitors,
        )
        self.assertEqual(result.assignments[0].primary_company_id, "UNKNOWN")

    def test_competitor_mode_requires_separate_raw_confirmed_registry(self) -> None:
        effective_scope = scope(
            competitors=[
                CompetitorInput(name="Meta", aliases=["Model Invented Alias"])
            ]
        )
        with self.assertRaisesRegex(
            CompanyAssignmentValidationError,
            "user_confirmed_competitors",
        ):
            assign_companies(
                [hit("P1", "Model Invented Alias")],
                effective_scope,
            )
        result = assign_companies(
            [hit("P1", "Model Invented Alias")],
            effective_scope,
            user_confirmed_competitors=[CompetitorInput(name="Meta")],
        )
        self.assertEqual(result.assignments[0].status, "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
