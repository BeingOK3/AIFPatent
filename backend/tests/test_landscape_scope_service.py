from __future__ import annotations

import asyncio
import unittest
from datetime import date

from landscape.scope import (
    CandidateSource,
    CandidateStatus,
    LandscapeInputMode,
    NameLanguage,
    ScopeDraftStatus,
    TechnologyTermRelation,
    make_technology_term_candidate,
)
from landscape.scope_expansion import fallback_company_scope
from landscape.scope_service import ScopeDraftPreparationService, ScopePreparationError


class MemoryRepository:
    def __init__(self):
        self.drafts = {}
        self.writes = []

    def find_company_memory(self, _input_name):
        return None

    def create(self, scope):
        self.drafts[scope.draft_id] = scope
        self.writes.append(scope)
        return scope

    def get(self, draft_id):
        return self.drafts[draft_id]

    def update(self, scope, *, expected_revision):
        current = self.drafts[scope.draft_id]
        if current.revision != expected_revision:
            raise AssertionError("stale test update")
        self.drafts[scope.draft_id] = scope
        self.writes.append(scope)
        return scope


class ExpansionStub:
    def __init__(self, *, fail_companies=(), fail_technology=False, delay=0.01):
        self.fail_companies = set(fail_companies)
        self.fail_technology = fail_technology
        self.delay = delay
        self.active = 0
        self.max_active = 0

    async def expand_company(self, input_name, *, memory=None):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delay)
            if input_name in self.fail_companies:
                raise RuntimeError("fixture company failure")
            return fallback_company_scope(input_name, memory=memory)
        finally:
            self.active -= 1

    async def expand_technology(self, original_input):
        await asyncio.sleep(self.delay)
        if self.fail_technology:
            raise RuntimeError("fixture technology failure")
        return (
            make_technology_term_candidate(
                text=original_input,
                language=NameLanguage.ZH,
                relation_to_original=TechnologyTermRelation.ORIGINAL,
                source=CandidateSource.USER_INPUT,
                status=CandidateStatus.ACTIVE,
            ),
            make_technology_term_candidate(
                text="liquid cooling",
                language=NameLanguage.EN,
                relation_to_original=TechnologyTermRelation.TRANSLATION,
                source=CandidateSource.MODEL_SUGGESTED,
                status=CandidateStatus.PROPOSED,
                rationale="英文翻译候选",
            ),
        )


class LandscapeScopePreparationServiceTests(unittest.TestCase):
    def test_create_is_persisted_before_any_model_expansion(self) -> None:
        repository = MemoryRepository()
        expansion = ExpansionStub()
        service = ScopeDraftPreparationService(repository, expansion)
        created = service.create_draft(
            company_names=("华为",),
            technology_input="数据中心液冷",
            publication_start=date(2010, 1, 1),
            publication_end=date(2026, 12, 31),
            draft_id="SCD-0000000000000005",
        )
        self.assertEqual(created.status, ScopeDraftStatus.DRAFT)
        self.assertEqual(created.revision, 1)
        self.assertEqual(expansion.max_active, 0)
        expanded = asyncio.run(
            service.expand_draft(created.draft_id, expected_revision=1)
        )
        self.assertEqual(expanded.status, ScopeDraftStatus.AWAITING_CONFIRMATION)
        self.assertEqual(expanded.revision, 3)

    def test_company_expansions_are_bounded_and_stage_revisions_are_persisted(self) -> None:
        repository = MemoryRepository()
        expansion = ExpansionStub()
        service = ScopeDraftPreparationService(
            repository,
            expansion,
            max_company_concurrency=2,
        )
        result = asyncio.run(
            service.prepare(
                company_names=("华为", "中兴", "小米", "OPPO", "vivo"),
                publication_start=date(2000, 1, 1),
                publication_end=date(2026, 12, 31),
                draft_id="SCD-0000000000000001",
            )
        )
        self.assertEqual(expansion.max_active, 2)
        self.assertEqual(
            [(item.revision, item.status) for item in repository.writes],
            [
                (1, ScopeDraftStatus.DRAFT),
                (2, ScopeDraftStatus.EXPANDING),
                (3, ScopeDraftStatus.AWAITING_CONFIRMATION),
            ],
        )
        self.assertEqual(result.mode, LandscapeInputMode.COMPANY_ONLY)
        self.assertEqual(result.publication_start, date(2000, 1, 1))
        self.assertEqual(result.publication_end, date(2026, 12, 31))

    def test_combined_failures_fall_back_per_object_and_remain_visible(self) -> None:
        repository = MemoryRepository()
        expansion = ExpansionStub(fail_companies={"中兴"}, fail_technology=True)
        result = asyncio.run(
            ScopeDraftPreparationService(repository, expansion).prepare(
                company_names=("华为", "中兴"),
                technology_input="数据中心液冷",
                publication_start=date(2010, 1, 1),
                publication_end=date(2025, 12, 31),
                draft_id="SCD-0000000000000002",
            )
        )
        self.assertEqual(result.mode, LandscapeInputMode.COMPANY_AND_TECHNOLOGY)
        self.assertEqual([item.text for item in result.companies[1].names], ["中兴"])
        self.assertEqual([item.text for item in result.technology_terms], ["数据中心液冷"])
        self.assertEqual(
            {item.code for item in result.limitations},
            {"COMPANY_EXPANSION_FAILED", "TECHNOLOGY_EXPANSION_FAILED"},
        )

    def test_technology_only_mode_has_no_company_tasks(self) -> None:
        expansion = ExpansionStub()
        result = asyncio.run(
            ScopeDraftPreparationService(MemoryRepository(), expansion).prepare(
                technology_input="数据中心液冷",
                publication_start=date(2020, 1, 1),
                publication_end=date(2026, 1, 1),
                draft_id="SCD-0000000000000003",
            )
        )
        self.assertEqual(result.mode, LandscapeInputMode.TECHNOLOGY_ONLY)
        self.assertEqual(expansion.max_active, 0)
        self.assertEqual(len(result.technology_terms), 2)

    def test_each_remote_object_has_an_independent_deadline_and_fallback(self) -> None:
        result = asyncio.run(
            ScopeDraftPreparationService(
                MemoryRepository(),
                ExpansionStub(delay=0.05),
                object_timeout_seconds=0.005,
            ).prepare(
                company_names=("华为",),
                technology_input="数据中心液冷",
                publication_start=date(2020, 1, 1),
                publication_end=date(2026, 1, 1),
                draft_id="SCD-0000000000000004",
            )
        )
        self.assertEqual(
            {item.code for item in result.limitations},
            {"COMPANY_EXPANSION_FAILED", "TECHNOLOGY_EXPANSION_FAILED"},
        )

    def test_interrupted_expansion_is_durable_and_resumable(self) -> None:
        async def scenario():
            repository = MemoryRepository()
            service = ScopeDraftPreparationService(
                repository,
                ExpansionStub(delay=0.2),
            )
            created = service.create_draft(
                company_names=("华为",),
                technology_input="数据中心液冷",
                publication_start=date(2000, 1, 1),
                publication_end=date(2026, 12, 31),
                draft_id="SCD-0000000000000006",
            )
            running = asyncio.create_task(
                service.expand_draft(created.draft_id, expected_revision=1)
            )
            while repository.get(created.draft_id).status != ScopeDraftStatus.EXPANDING:
                await asyncio.sleep(0)
            running.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await running

            interrupted = repository.get(created.draft_id)
            self.assertEqual(interrupted.status, ScopeDraftStatus.EXPANDING)
            self.assertEqual(interrupted.revision, 2)

            service.expansion = ExpansionStub(delay=0)
            resumed = await service.expand_draft(
                created.draft_id,
                expected_revision=2,
            )
            self.assertEqual(resumed.status, ScopeDraftStatus.AWAITING_CONFIRMATION)
            self.assertEqual(resumed.revision, 3)

        asyncio.run(scenario())

    def test_invalid_empty_duplicate_or_reversed_inputs_fail_before_persistence(self) -> None:
        cases = (
            {"company_names": (), "technology_input": None},
            {"company_names": ("华为", " 华为 "), "technology_input": None},
        )
        for index, values in enumerate(cases):
            repository = MemoryRepository()
            with self.subTest(index=index), self.assertRaises(ScopePreparationError):
                asyncio.run(
                    ScopeDraftPreparationService(repository, ExpansionStub()).prepare(
                        **values,
                        publication_start=date(2025, 1, 1),
                        publication_end=date(2025, 12, 31),
                    )
                )
            self.assertEqual(repository.writes, [])

        repository = MemoryRepository()
        with self.assertRaisesRegex(ScopePreparationError, "publication_end"):
            asyncio.run(
                ScopeDraftPreparationService(repository, ExpansionStub()).prepare(
                    company_names=("华为",),
                    publication_start=date(2025, 2, 1),
                    publication_end=date(2025, 1, 1),
                )
            )
        self.assertEqual(repository.writes, [])


if __name__ == "__main__":
    unittest.main()
