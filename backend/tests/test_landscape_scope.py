from __future__ import annotations

import unittest
from datetime import date

from pydantic import ValidationError

from landscape.scope import (
    CandidateSource,
    CandidateStatus,
    CompanyNameRelation,
    CompanyScopeDraft,
    LandscapeInputMode,
    NameLanguage,
    ScopeDraft,
    ScopeDraftStatus,
    ScopeValidationError,
    TechnologyTermRelation,
    freeze_scope_draft,
    make_company_name_candidate,
    make_company_profile_id,
    make_scope_draft_id,
    make_technology_term_candidate,
)


def company(
    name: str = "华为",
    *,
    english: str = "Huawei Technologies Co., Ltd.",
    statuses: tuple[CandidateStatus, CandidateStatus] = (
        CandidateStatus.ACTIVE,
        CandidateStatus.ACTIVE,
    ),
) -> CompanyScopeDraft:
    profile_id = make_company_profile_id(name)
    return CompanyScopeDraft(
        profile_id=profile_id,
        display_name=name,
        input_name=name,
        names=(
            make_company_name_candidate(
                profile_id=profile_id,
                text=name,
                language=NameLanguage.ZH,
                relation_type=CompanyNameRelation.LEGAL_NAME,
                source=CandidateSource.USER_INPUT,
                status=statuses[0],
            ),
            make_company_name_candidate(
                profile_id=profile_id,
                text=english,
                language=NameLanguage.EN,
                relation_type=CompanyNameRelation.TRANSLATION,
                source=CandidateSource.MODEL_SUGGESTED,
                status=statuses[1],
            ),
        ),
    )


def terms(
    *,
    zh_status: CandidateStatus = CandidateStatus.ACTIVE,
    en_status: CandidateStatus = CandidateStatus.ACTIVE,
):
    return (
        make_technology_term_candidate(
            text="无线通信",
            language=NameLanguage.ZH,
            relation_to_original=TechnologyTermRelation.ORIGINAL,
            source=CandidateSource.USER_INPUT,
            status=zh_status,
        ),
        make_technology_term_candidate(
            text="wireless communication",
            language=NameLanguage.EN,
            relation_to_original=TechnologyTermRelation.TRANSLATION,
            source=CandidateSource.MODEL_SUGGESTED,
            status=en_status,
        ),
    )


def draft(**changes) -> ScopeDraft:
    values = {
        "draft_id": make_scope_draft_id("scope-fixture"),
        "revision": 3,
        "status": ScopeDraftStatus.AWAITING_CONFIRMATION,
        "publication_start": date(2020, 1, 1),
        "publication_end": date(2025, 12, 31),
        "companies": (company(),),
        "technology_input": "无线通信",
        "technology_terms": terms(),
    }
    values.update(changes)
    return ScopeDraft(**values)


class LandscapeScopeTests(unittest.TestCase):
    def test_three_modes_are_derived_from_inputs(self) -> None:
        cases = (
            (draft(technology_input=None, technology_terms=()), LandscapeInputMode.COMPANY_ONLY),
            (draft(companies=()), LandscapeInputMode.TECHNOLOGY_ONLY),
            (draft(), LandscapeInputMode.COMPANY_AND_TECHNOLOGY),
        )
        for scope, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(scope.mode, expected)

    def test_long_user_selected_publication_window_is_preserved(self) -> None:
        scope = draft(
            publication_start=date(2010, 1, 1),
            publication_end=date(2025, 12, 31),
        )
        frozen = freeze_scope_draft(scope)
        self.assertEqual(frozen.publication_start, date(2010, 1, 1))
        self.assertEqual(frozen.publication_end, date(2025, 12, 31))

    def test_publication_date_boundaries_must_be_ordered(self) -> None:
        with self.assertRaisesRegex(ValidationError, "publication_end"):
            draft(
                publication_start=date(2025, 1, 2),
                publication_end=date(2025, 1, 1),
            )

    def test_confirmation_requires_explicit_review_of_every_suggestion(self) -> None:
        scope = draft(companies=(company(statuses=(CandidateStatus.ACTIVE, CandidateStatus.PROPOSED)),))
        with self.assertRaisesRegex(ScopeValidationError, "must be reviewed"):
            freeze_scope_draft(scope)

    def test_each_company_requires_an_active_name(self) -> None:
        excluded = company(
            statuses=(CandidateStatus.EXCLUDED, CandidateStatus.EXCLUDED)
        )
        with self.assertRaisesRegex(ScopeValidationError, "at least one active name"):
            freeze_scope_draft(
                draft(
                    companies=(excluded,),
                    technology_input=None,
                    technology_terms=(),
                )
            )

    def test_technology_confirmation_requires_zh_and_en(self) -> None:
        only_zh = terms(en_status=CandidateStatus.EXCLUDED)
        with self.assertRaisesRegex(ScopeValidationError, "active ZH and EN"):
            freeze_scope_draft(draft(companies=(), technology_terms=only_zh))

    def test_active_name_cannot_belong_to_two_company_scopes(self) -> None:
        first = company("华为")
        second_profile = make_company_profile_id("华为终端")
        shared = make_company_name_candidate(
            profile_id=second_profile,
            text="华为",
            language=NameLanguage.ZH,
            relation_type=CompanyNameRelation.ALIAS,
            source=CandidateSource.USER_ADDED,
            status=CandidateStatus.ACTIVE,
        )
        second = CompanyScopeDraft(
            profile_id=second_profile,
            display_name="华为终端",
            input_name="华为终端",
            names=(shared,),
        )
        with self.assertRaisesRegex(ScopeValidationError, "multiple companies"):
            freeze_scope_draft(
                draft(
                    companies=(first, second),
                    technology_input=None,
                    technology_terms=(),
                )
            )

    def test_alias_and_subsidiary_are_distinct_relations(self) -> None:
        self.assertNotEqual(
            CompanyNameRelation.ALIAS,
            CompanyNameRelation.SUBSIDIARY,
        )
        item = make_company_name_candidate(
            profile_id=make_company_profile_id("华为"),
            text="荣耀终端有限公司",
            language=NameLanguage.ZH,
            relation_type=CompanyNameRelation.SUBSIDIARY,
            source=CandidateSource.USER_ADDED,
        )
        self.assertEqual(item.relation_type, CompanyNameRelation.SUBSIDIARY)

    def test_frozen_revision_is_deterministic_and_contains_only_active_entries(self) -> None:
        profile_id = make_company_profile_id("华为")
        active = make_company_name_candidate(
            profile_id=profile_id,
            text="华为技术有限公司",
            language=NameLanguage.ZH,
            relation_type=CompanyNameRelation.LEGAL_NAME,
            source=CandidateSource.USER_ADDED,
            status=CandidateStatus.ACTIVE,
        )
        excluded = make_company_name_candidate(
            profile_id=profile_id,
            text="荣耀终端有限公司",
            language=NameLanguage.ZH,
            relation_type=CompanyNameRelation.SUBSIDIARY,
            source=CandidateSource.MODEL_SUGGESTED,
            status=CandidateStatus.EXCLUDED,
        )
        scope = draft(
            companies=(
                CompanyScopeDraft(
                    profile_id=profile_id,
                    display_name="华为",
                    input_name="华为",
                    names=(active, excluded),
                ),
            ),
            technology_input=None,
            technology_terms=(),
        )
        first = freeze_scope_draft(scope)
        second = freeze_scope_draft(scope)
        self.assertEqual(first, second)
        self.assertEqual(first.scope_revision_id[:4], "SCR-")
        self.assertEqual([name.text for name in first.companies[0].names], [active.text])

    def test_draft_and_revision_models_reject_secret_fields(self) -> None:
        payload = draft().model_dump(mode="json")
        payload["api_key"] = "must-not-be-accepted"
        with self.assertRaises(ValidationError):
            ScopeDraft.model_validate(payload)


if __name__ == "__main__":
    unittest.main()
