from __future__ import annotations

import asyncio
import unittest
from datetime import date
from types import SimpleNamespace

from idea.providers.base import FetchedDocument, SearchHit
from landscape.patent_snapshot import make_snapshot_set, snapshot_from_document
from landscape.publication_freeze import freeze_publications
from landscape.stage_repository import STAGE_ORDER, V4StageStatus
from landscape.v4_run import LandscapeRunStatus
from landscape.v4_workflow import V4LandscapeWorkflow, V4WorkflowOutcome
from landscape.classification_terminal import (
    ClassificationAction,
    ClassificationResult,
    ClassificationTerminal,
)
from landscape.direction_record import DirectionRecord, DirectionStatus
from landscape.taxonomy import compile_taxonomy_markdown
from tests.test_landscape_query_planning import confirmed_scope


class RunRepository:
    def __init__(self, scope_id, status=LandscapeRunStatus.PLANNING):
        self.value = SimpleNamespace(
            status=status,
            scope_revision_id=scope_id,
            taxonomy_version="TAX-TEST",
            publication_start=date(2020, 1, 1),
            publication_end=date(2025, 12, 31),
        )

    def get(self, run_id):
        return self.value

    def transition(self, run_id, target, *, expected, **kwargs):
        if self.value.status not in expected:
            raise ValueError("unexpected transition")
        self.value = SimpleNamespace(
            status=LandscapeRunStatus(target),
            scope_revision_id=self.value.scope_revision_id,
            taxonomy_version=self.value.taxonomy_version,
            publication_start=self.value.publication_start,
            publication_end=self.value.publication_end,
        )
        return self.value


class StageRepository:
    def __init__(self):
        self.statuses = {name: V4StageStatus.PENDING for name in STAGE_ORDER}
        self.events = []
        self.limitations = []

    def ensure(self, run_id):
        return self.list(run_id)

    def list(self, run_id):
        return tuple(
            SimpleNamespace(stage_name=name, status=self.statuses[name])
            for name in STAGE_ORDER
        )

    def start(self, run_id, name, **kwargs):
        index = STAGE_ORDER.index(name)
        if any(
            self.statuses[previous]
            not in {V4StageStatus.SUCCEEDED, V4StageStatus.SUCCEEDED_WITH_LIMITATIONS}
            for previous in STAGE_ORDER[:index]
        ):
            raise ValueError("predecessor incomplete")
        self.statuses[name] = V4StageStatus.RUNNING
        self.events.append(("start", name))

    def progress(self, run_id, name, **kwargs):
        self.events.append(("progress", name, kwargs["completed_count"]))

    def succeed(self, run_id, name, *, with_limitations=False):
        self.statuses[name] = (
            V4StageStatus.SUCCEEDED_WITH_LIMITATIONS
            if with_limitations
            else V4StageStatus.SUCCEEDED
        )
        self.events.append(("succeed", name))

    def add_limitation(self, run_id, name, **kwargs):
        self.limitations.append((name, kwargs))


class SearchCoordinator:
    def __init__(self, run_repository, frozen, *, waits=False):
        self.run_repository = run_repository
        self.frozen = frozen
        self.waits = waits
        self.query_repository = SimpleNamespace(
            get=lambda run_id: SimpleNamespace(queries=(SimpleNamespace(query_id="Q-1"),))
        )
        self.calls = []

    async def estimate(self, run_id, provider):
        self.calls.append("estimate")
        self.run_repository.value = SimpleNamespace(
            status=(
                LandscapeRunStatus.AWAITING_SCALE_CONFIRMATION
                if self.waits
                else LandscapeRunStatus.READY
            ),
            scope_revision_id=self.run_repository.value.scope_revision_id,
            taxonomy_version=self.run_repository.value.taxonomy_version,
            publication_start=self.run_repository.value.publication_start,
            publication_end=self.run_repository.value.publication_end,
        )

    async def retrieve(self, run_id, provider):
        self.calls.append("retrieve")
        return (SimpleNamespace(query_id="Q-1"),)

    def freeze(self, run_id, results):
        self.calls.append("freeze")
        return self.frozen


class SnapshotFetch:
    def __init__(self, frozen, repository):
        self.frozen = frozen
        self.repository = repository
        self.calls = 0

    async def fetch(self, frozen, *, progress):
        self.calls += 1
        item = frozen.publications[0]
        snapshot = snapshot_from_document(
            item,
            FetchedDocument(
                provider="details",
                publication_number=item.publication_number,
                application_number="US10/001",
                assignees=["Huawei Technologies Co., Ltd."],
                url=item.url,
                abstract_text="A sufficiently detailed technical abstract for stable classification.",
            ),
        )
        self.repository.value = make_snapshot_set(frozen, (snapshot,))
        await progress(1, 1)
        return self.repository.value


class ValueRepository:
    def __init__(self, value=None):
        self.value = value
        self.puts = []

    def get(self, run_id):
        return self.value

    def put(self, *values):
        self.puts.append(values)
        self.value = values[-1]
        return self.value


class KeyedRepository:
    def __init__(self):
        self.values = {}

    def put(self, run_id, value):
        self.values[value.analysis_unit_id if hasattr(value, "analysis_unit_id") else value.publication_id] = value
        return value

    def get(self, run_id, item_id):
        return self.values[item_id]


class DirectionService:
    async def extract(self, packets, taxonomy):
        level1 = next(node.category_id for node in taxonomy.nodes if node.level == 1)
        return tuple(
            DirectionRecord(
                analysis_unit_id=packet.analysis_unit_id,
                status=DirectionStatus.AVAILABLE,
                evidence_sufficient=True,
                solution_mechanism="通过液体回路散热",
                direction_summary="冷板液冷方向",
                candidate_level1_ids=(level1,),
                confidence=0.8,
                evidence_ids=(packet.evidence_ids[0],),
            )
            for packet in packets
        )


class ClassificationService:
    async def classify(self, directions, taxonomy):
        return tuple(
            ClassificationResult(
                analysis_unit_id=item.analysis_unit_id,
                action=ClassificationAction.NONE_OF_CANDIDATES,
                terminal=ClassificationTerminal.OTHERS,
                evidence_ids=item.evidence_ids,
                confidence=0.7,
                review_round=0,
            )
            for item in directions
        )


SEMANTIC_TAXONOMY = compile_taxonomy_markdown(
    """| 一级分类 | 二级分类 | 三级分类 |
| --- | --- | --- |
| Hardware | Cooling | Liquid |
"""
)


class V4LandscapeWorkflowTests(unittest.TestCase):
    def make_workflow(self, *, waits=False):
        scope = confirmed_scope(companies=(("华为", ("Huawei Technologies Co., Ltd.",)),))
        frozen = freeze_publications(
            "LRN-0123456789abcdef",
            ((
                "Q-1",
                SearchHit(
                    provider="search",
                    provider_rank=1,
                    publication_number="US1A1",
                    url="https://example.test/US1A1",
                ),
            ),),
        )
        run_repository = RunRepository(scope.scope_revision_id)
        stages = StageRepository()
        search = SearchCoordinator(run_repository, frozen, waits=waits)
        snapshot_repository = ValueRepository()
        snapshot_fetch = SnapshotFetch(frozen, snapshot_repository)
        publication_repository = ValueRepository(frozen)
        family_repository = ValueRepository()
        abstract_repository = ValueRepository()
        organization_repository = ValueRepository()
        workflow = V4LandscapeWorkflow(
            run_repository=run_repository,
            scope_repository=SimpleNamespace(get_confirmed=lambda scope_id: scope),
            stage_repository=stages,
            search_coordinator=search,
            publication_repository=publication_repository,
            snapshot_fetch=snapshot_fetch,
            snapshot_repository=snapshot_repository,
            family_repository=family_repository,
            abstract_repository=abstract_repository,
            organization_repository=organization_repository,
            provider=SimpleNamespace(name="paged"),
        )
        return workflow, run_repository, stages, search, snapshot_fetch, family_repository, abstract_repository, organization_repository

    def test_preanalysis_stages_are_serial_and_resume_without_refetching_details(self):
        values = self.make_workflow()
        workflow, run_repository, stages, search, snapshot_fetch, families, abstracts, organizations = values
        outcome = asyncio.run(workflow.execute_preanalysis("LRN-0123456789abcdef"))
        self.assertEqual(outcome, V4WorkflowOutcome.PREANALYSIS_COMPLETE)
        self.assertEqual(run_repository.value.status, LandscapeRunStatus.RUNNING)
        self.assertEqual(
            [event[1] for event in stages.events if event[0] == "start"],
            list(STAGE_ORDER[:5]),
        )
        self.assertEqual(snapshot_fetch.calls, 1)
        self.assertEqual(len(families.puts), 1)
        self.assertEqual(len(abstracts.puts), 1)
        self.assertEqual(len(organizations.puts), 1)

        second = asyncio.run(workflow.execute_preanalysis("LRN-0123456789abcdef"))
        self.assertEqual(second, V4WorkflowOutcome.PREANALYSIS_COMPLETE)
        self.assertEqual(snapshot_fetch.calls, 1)
        self.assertEqual(search.calls.count("estimate"), 1)
        self.assertEqual(search.calls.count("freeze"), 1)

    def test_scale_gate_stops_before_retrieval(self):
        workflow, run_repository, stages, search, *_ = self.make_workflow(waits=True)
        outcome = asyncio.run(workflow.execute_preanalysis("LRN-0123456789abcdef"))
        self.assertEqual(outcome, V4WorkflowOutcome.AWAITING_SCALE_CONFIRMATION)
        self.assertEqual(
            run_repository.value.status,
            LandscapeRunStatus.AWAITING_SCALE_CONFIRMATION,
        )
        self.assertEqual(search.calls, ["estimate"])
        self.assertEqual(stages.statuses[STAGE_ORDER[1]], V4StageStatus.PENDING)

    def test_semantic_stages_follow_preanalysis_and_persist_every_unit(self):
        values = self.make_workflow()
        workflow, _, stages, _, _, _, old_abstracts, _ = values
        asyncio.run(workflow.execute_preanalysis("LRN-0123456789abcdef"))
        abstract_repository = KeyedRepository()
        for _run_id, evidence in old_abstracts.puts:
            abstract_repository.put(_run_id, evidence)
        direction_repository = KeyedRepository()
        classification_repository = KeyedRepository()
        others_repository = ValueRepository()
        workflow.abstract_repository = abstract_repository
        workflow.direction_extraction = DirectionService()
        workflow.classification_matching = ClassificationService()
        workflow.direction_repository = direction_repository
        workflow.classification_repository = classification_repository
        workflow.taxonomy_repository = SimpleNamespace(get=lambda version: SEMANTIC_TAXONOMY)
        workflow.others_repository = others_repository

        asyncio.run(workflow.execute_semantics("LRN-0123456789abcdef"))

        self.assertEqual(stages.statuses[STAGE_ORDER[5]], V4StageStatus.SUCCEEDED)
        self.assertEqual(stages.statuses[STAGE_ORDER[6]], V4StageStatus.SUCCEEDED)
        self.assertEqual(stages.statuses[STAGE_ORDER[7]], V4StageStatus.SUCCEEDED)
        self.assertEqual(len(direction_repository.values), 1)
        self.assertEqual(len(classification_repository.values), 1)
        self.assertEqual(len(others_repository.puts), 1)

        metric_repository = ValueRepository()
        trend_repository = ValueRepository()
        representative_repository = ValueRepository()
        workflow.metric_repository = metric_repository
        workflow.trend_repository = trend_repository
        workflow.representative_repository = representative_repository
        workflow.execute_analytics("LRN-0123456789abcdef")
        self.assertEqual(stages.statuses[STAGE_ORDER[8]], V4StageStatus.SUCCEEDED_WITH_LIMITATIONS)
        self.assertEqual(stages.statuses[STAGE_ORDER[9]], V4StageStatus.SUCCEEDED)
        self.assertEqual(stages.statuses[STAGE_ORDER[10]], V4StageStatus.SUCCEEDED)
        self.assertEqual(len(metric_repository.puts), 1)
        self.assertEqual(len(trend_repository.puts), 1)
        self.assertEqual(len(representative_repository.puts), 1)


if __name__ == "__main__":
    unittest.main()
