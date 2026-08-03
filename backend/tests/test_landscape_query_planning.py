from __future__ import annotations

import unittest
from datetime import date

from landscape.query_planning import build_query_plan
from landscape.scope import (
    CandidateSource,
    CandidateStatus,
    CompanyNameRelation,
    CompanyScopeDraft,
    NameLanguage,
    ScopeDraft,
    ScopeDraftStatus,
    TechnologyTermRelation,
    freeze_scope_draft,
    make_company_name_candidate,
    make_company_profile_id,
    make_scope_draft_id,
    make_technology_term_candidate,
)


def confirmed_scope(*, companies=(), technology_terms=()):
    company_values = []
    versions = {}
    for input_name, names in companies:
        profile_id = make_company_profile_id(input_name)
        versions[profile_id] = 1
        company_values.append(
            CompanyScopeDraft(
                profile_id=profile_id,
                display_name=input_name,
                input_name=input_name,
                names=tuple(
                    make_company_name_candidate(
                        profile_id=profile_id,
                        text=value,
                        language=(
                            NameLanguage.ZH
                            if any("\u3400" <= char <= "\u9fff" for char in value)
                            else NameLanguage.EN
                        ),
                        relation_type=CompanyNameRelation.ALIAS,
                        source=CandidateSource.USER_INPUT,
                        status=CandidateStatus.ACTIVE,
                    )
                    for value in names
                ),
            )
        )
    terms = tuple(
        make_technology_term_candidate(
            text=value,
            language=language,
            relation_to_original=(
                TechnologyTermRelation.ORIGINAL
                if index == 0
                else TechnologyTermRelation.RELATED
            ),
            source=CandidateSource.USER_INPUT,
            status=CandidateStatus.ACTIVE,
        )
        for index, (value, language) in enumerate(technology_terms)
    )
    draft = ScopeDraft(
        draft_id=make_scope_draft_id(
            repr((companies, technology_terms))
        ),
        revision=7,
        status=ScopeDraftStatus.AWAITING_CONFIRMATION,
        publication_start=date(1990, 1, 1),
        publication_end=date(2026, 12, 31),
        companies=tuple(company_values),
        technology_input="测试技术方向" if terms else None,
        technology_terms=terms,
    )
    return freeze_scope_draft(draft, versions)


class LandscapeV4QueryPlanningTests(unittest.TestCase):
    def test_company_only_gives_every_confirmed_name_an_independent_query(self) -> None:
        scope = confirmed_scope(
            companies=(
                ("华为", ("华为", "Huawei Technologies")),
                ("中兴", ("中兴", "ZTE Corporation")),
            )
        )
        plan = build_query_plan(scope)
        self.assertEqual(len(plan.queries), 4)
        self.assertEqual(
            {query.company_name for query in plan.queries},
            {"华为", "Huawei Technologies", "中兴", "ZTE Corporation"},
        )
        self.assertTrue(all(not query.terms for query in plan.queries))
        self.assertTrue(all(query.publication_start == date(1990, 1, 1) for query in plan.queries))

    def test_technology_terms_split_deterministically_without_loss(self) -> None:
        values = tuple(
            (f"技术词{index}", NameLanguage.ZH)
            if index % 2
            else (f"technology term {index}", NameLanguage.EN)
            for index in range(18)
        )
        scope = confirmed_scope(technology_terms=values)
        first = build_query_plan(scope, max_terms_per_group=8)
        second = build_query_plan(scope, max_terms_per_group=8)
        self.assertEqual(first, second)
        self.assertEqual([len(query.terms) for query in first.queries], [8, 8, 2])
        self.assertEqual(
            [term for query in first.queries for term in query.terms],
            [value for value, _language in values],
        )

    def test_combined_mode_is_company_name_by_term_group_cartesian_product(self) -> None:
        values = tuple(
            (f"方向{index}", NameLanguage.ZH)
            if index % 2
            else (f"direction {index}", NameLanguage.EN)
            for index in range(10)
        )
        scope = confirmed_scope(
            companies=(
                ("华为", ("华为", "Huawei")),
                ("中兴", ("中兴", "ZTE")),
            ),
            technology_terms=values,
        )
        plan = build_query_plan(scope, max_terms_per_group=4)
        self.assertEqual(len(plan.queries), 4 * 3)
        counts = {
            name: sum(query.company_name == name for query in plan.queries)
            for name in ("华为", "Huawei", "中兴", "ZTE")
        }
        self.assertEqual(set(counts.values()), {3})
        self.assertEqual(len({query.query_hash for query in plan.queries}), 12)

    def test_query_text_escaping_does_not_change_structured_identity(self) -> None:
        scope = confirmed_scope(companies=(("Example", ('Example "Lab"',)),))
        query = build_query_plan(scope).queries[0]
        self.assertIn('Example \\"Lab\\"', query.query_text)
        self.assertEqual(query.company_name, 'Example "Lab"')

    def test_group_limits_are_bounded(self) -> None:
        scope = confirmed_scope(
            technology_terms=(("液冷", NameLanguage.ZH), ("liquid cooling", NameLanguage.EN))
        )
        for values in (
            {"max_terms_per_group": 0},
            {"max_terms_per_group": 9},
            {"max_group_characters": 99},
            {"max_group_characters": 901},
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                build_query_plan(scope, **values)


if __name__ == "__main__":
    unittest.main()
