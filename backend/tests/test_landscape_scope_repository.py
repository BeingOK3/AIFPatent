from __future__ import annotations

import copy
import json
import unittest
from datetime import date

from landscape.scope import (
    CandidateSource,
    CandidateStatus,
    CompanyNameRelation,
    CompanyScopeDraft,
    NameLanguage,
    ScopeDraft,
    ScopeDraftStatus,
    TechnologyTermRelation,
    make_company_name_candidate,
    make_company_profile_id,
    make_scope_draft_id,
    make_technology_term_candidate,
)
from landscape.scope_repository import (
    ScopePersistenceError,
    prepare_scope_draft_rows,
    validate_persisted_scope_draft,
)


def fixture() -> ScopeDraft:
    profile_id = make_company_profile_id("华为")
    names = (
        make_company_name_candidate(
            profile_id=profile_id,
            text="华为技术有限公司",
            language=NameLanguage.ZH,
            relation_type=CompanyNameRelation.LEGAL_NAME,
            source=CandidateSource.USER_INPUT,
            status=CandidateStatus.ACTIVE,
        ),
        make_company_name_candidate(
            profile_id=profile_id,
            text="Huawei Technologies Co., Ltd.",
            language=NameLanguage.EN,
            relation_type=CompanyNameRelation.TRANSLATION,
            source=CandidateSource.MODEL_SUGGESTED,
            status=CandidateStatus.PROPOSED,
        ),
    )
    terms = (
        make_technology_term_candidate(
            text="无线通信",
            language=NameLanguage.ZH,
            relation_to_original=TechnologyTermRelation.ORIGINAL,
            source=CandidateSource.USER_INPUT,
            status=CandidateStatus.ACTIVE,
        ),
        make_technology_term_candidate(
            text="wireless communication",
            language=NameLanguage.EN,
            relation_to_original=TechnologyTermRelation.TRANSLATION,
            source=CandidateSource.MODEL_SUGGESTED,
            status=CandidateStatus.PROPOSED,
        ),
    )
    return ScopeDraft(
        draft_id=make_scope_draft_id("repository-fixture"),
        revision=2,
        status=ScopeDraftStatus.AWAITING_CONFIRMATION,
        publication_start=date(2001, 1, 1),
        publication_end=date(2025, 12, 31),
        companies=(
            CompanyScopeDraft(
                profile_id=profile_id,
                display_name="华为",
                input_name="华为",
                names=names,
            ),
        ),
        technology_input="无线通信",
        technology_terms=terms,
    )


class LandscapeScopeRowCodecTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scope = fixture()
        self.rows = prepare_scope_draft_rows(self.scope, created_at=1234)

    def test_rows_are_complete_deterministic_and_keep_full_date_range(self) -> None:
        duplicate = prepare_scope_draft_rows(self.scope, created_at=1234)
        self.assertEqual(duplicate, self.rows)
        self.assertEqual(self.rows.draft["publication_start"], "2001-01-01")
        self.assertEqual(self.rows.draft["publication_end"], "2025-12-31")
        self.assertEqual(len(self.rows.draft["content_hash"]), 64)
        self.assertEqual(len(self.rows.companies), 1)
        self.assertEqual(len(self.rows.names), 2)
        self.assertEqual(len(self.rows.terms), 2)

    def test_relational_rows_round_trip_to_validated_scope(self) -> None:
        stored = validate_persisted_scope_draft(
            self.rows.draft,
            self.rows.revision,
            self.rows.companies,
            self.rows.names,
            self.rows.terms,
        )
        self.assertEqual(stored, self.scope)

    def test_json_string_from_database_driver_is_supported(self) -> None:
        revision = copy.deepcopy(self.rows.revision)
        revision["content_json"] = json.dumps(
            revision["content_json"], ensure_ascii=False
        )
        self.assertEqual(
            validate_persisted_scope_draft(
                self.rows.draft,
                revision,
                self.rows.companies,
                self.rows.names,
                self.rows.terms,
            ),
            self.scope,
        )

    def test_header_or_revision_tampering_fails_closed(self) -> None:
        mutations = []
        draft_row = copy.deepcopy(self.rows.draft)
        draft_row["mode"] = "COMPANY_ONLY"
        mutations.append((draft_row, self.rows.revision))
        revision_row = copy.deepcopy(self.rows.revision)
        revision_row["content_hash"] = "0" * 64
        mutations.append((self.rows.draft, revision_row))
        for draft_value, revision_value in mutations:
            with self.subTest(draft=draft_value, revision=revision_value):
                with self.assertRaises(ScopePersistenceError):
                    validate_persisted_scope_draft(
                        draft_value,
                        revision_value,
                        self.rows.companies,
                        self.rows.names,
                        self.rows.terms,
                    )

    def test_missing_reordered_or_modified_relational_row_fails_closed(self) -> None:
        cases = []
        cases.append((self.rows.names[:-1], self.rows.terms))
        cases.append((tuple(reversed(self.rows.names)), self.rows.terms))
        changed_terms = list(copy.deepcopy(self.rows.terms))
        changed_terms[0]["term_text"] = "篡改"
        cases.append((self.rows.names, tuple(changed_terms)))
        for names, terms in cases:
            with self.subTest(names=names, terms=terms):
                with self.assertRaises(ScopePersistenceError):
                    validate_persisted_scope_draft(
                        self.rows.draft,
                        self.rows.revision,
                        self.rows.companies,
                        names,
                        terms,
                    )

    def test_invalid_timestamp_is_rejected(self) -> None:
        for value in (-1, True, 1.5):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    prepare_scope_draft_rows(self.scope, created_at=value)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
