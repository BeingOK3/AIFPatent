from __future__ import annotations

import unittest
from datetime import date

from landscape.organization_assignment import (
    UNKNOWN_ORGANIZATION_ID,
    OrganizationType,
    assign_organizations,
)
from landscape.scope import (
    CompanyNameRelation,
    ConfirmedCompanyName,
    ConfirmedCompanyScope,
    ConfirmedScopeRevision,
    LandscapeInputMode,
    NameLanguage,
)


def _scope(mode: LandscapeInputMode) -> ConfirmedScopeRevision:
    companies = ()
    if mode != LandscapeInputMode.TECHNOLOGY_ONLY:
        companies = (
            ConfirmedCompanyScope(
                profile_id="CMP-0123456789abcdef",
                profile_version=3,
                display_name="华为",
                names=(
                    ConfirmedCompanyName(
                        name_id="CNM-0123456789abcdef",
                        text="华为技术有限公司",
                        normalized_text="华为技术有限公司",
                        language=NameLanguage.ZH,
                        relation_type=CompanyNameRelation.LEGAL_NAME,
                    ),
                    ConfirmedCompanyName(
                        name_id="CNM-fedcba9876543210",
                        text="Huawei Technologies Co., Ltd.",
                        normalized_text="huawei technologies co., ltd.",
                        language=NameLanguage.EN,
                        relation_type=CompanyNameRelation.TRANSLATION,
                    ),
                ),
            ),
        )
    return ConfirmedScopeRevision(
        scope_revision_id="SCR-0123456789abcdef",
        scope_revision_hash="0" * 64,
        source_draft_id="SCD-0123456789abcdef",
        source_draft_revision=1,
        mode=mode,
        publication_start=date(2020, 1, 1),
        publication_end=date(2024, 12, 31),
        companies=companies,
        technology_input="wireless" if mode != LandscapeInputMode.COMPANY_ONLY else None,
        technology_terms=(),
    )


class OrganizationAssignmentV4Tests(unittest.TestCase):
    def test_company_mode_uses_only_confirmed_exact_names_and_preserves_all_applicants(self):
        result = assign_organizations(
            "RUN-1",
            _scope(LandscapeInputMode.COMPANY_AND_TECHNOLOGY),
            {
                "PUB-0123456789abcdef": (
                    "Huawei Technologies Co., Ltd.",
                    "Tsinghua University",
                ),
                "PUB-fedcba9876543210": ("Huawei Device Co., Ltd.",),
            },
        )

        first, second = result.assignments
        self.assertNotEqual(first.primary_organization_id, UNKNOWN_ORGANIZATION_ID)
        self.assertEqual(first.co_organization_ids, ())
        self.assertEqual(first.unconfirmed_applicants, ("Tsinghua University",))
        self.assertEqual(second.primary_organization_id, UNKNOWN_ORGANIZATION_ID)
        self.assertEqual(second.unconfirmed_applicants, ("Huawei Device Co., Ltd.",))
        self.assertEqual(len(first.observed_applicants), 2)

    def test_technology_mode_groups_only_equal_normalized_names(self):
        result = assign_organizations(
            "RUN-2",
            _scope(LandscapeInputMode.TECHNOLOGY_ONLY),
            {
                "PUB-0123456789abcdef": ("Tsinghua University", "Acme Ltd"),
                "PUB-fedcba9876543210": ("  TSINGHUA   UNIVERSITY ",),
                "PUB-aaaaaaaaaaaaaaaa": ("Tsinghua University Press",),
            },
        )

        by_name = {item.normalized_name: item for item in result.organizations}
        self.assertEqual(len(result.organizations), 3)
        self.assertEqual(
            by_name["tsinghua university"].organization_type,
            OrganizationType.ACADEMIC_RESEARCH,
        )
        self.assertEqual(by_name["acme ltd"].organization_type, OrganizationType.COMPANY)
        assignments = {item.publication_id: item for item in result.assignments}
        self.assertNotEqual(
            assignments["PUB-fedcba9876543210"].primary_organization_id,
            assignments["PUB-aaaaaaaaaaaaaaaa"].primary_organization_id,
        )

    def test_missing_applicant_is_explicit_unknown(self):
        result = assign_organizations(
            "RUN-3",
            _scope(LandscapeInputMode.TECHNOLOGY_ONLY),
            {"PUB-0123456789abcdef": ()},
        )
        self.assertEqual(result.assignments[0].primary_organization_id, UNKNOWN_ORGANIZATION_ID)
        self.assertEqual(result.organizations[0].organization_type, OrganizationType.UNKNOWN)


if __name__ == "__main__":
    unittest.main()
