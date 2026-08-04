from __future__ import annotations

import asyncio
import resource
import time
import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from backend.tests.landscape_v4_fixture_factory import INPUT_MODES, make_scale_case
from landscape.batching import make_profile
from landscape.classification_agent import (
    CLASSIFICATION_AGENT_NAME,
    ClassificationBatchOutput,
    ClassificationDecision,
    ClassificationMatchingService,
)
from landscape.classification_terminal import (
    ClassificationAction,
    ClassificationTerminal,
    reconcile_terminals,
)
from landscape.direction_agent import (
    DIRECTION_AGENT_NAME,
    DirectionBatchOutput,
    DirectionExtractionService,
    DirectionUnitPacket,
)
from landscape.direction_record import DirectionRecord, DirectionStatus
from landscape.model_scheduler import ModelBudget, ModelScheduler
from landscape.taxonomy import compile_taxonomy_file


ROOT = Path(__file__).resolve().parents[2]


class DeterministicPressureModel:
    """Fast remote-model surrogate that still exercises real batch validation."""

    def __init__(self) -> None:
        self.calls = 0
        self.agent_calls: Counter[str] = Counter()
        self.active = 0
        self.peak_active = 0

    async def complete(self, agent_name, *, input_payload, **_kwargs):
        self.calls += 1
        self.agent_calls[agent_name] += 1
        self.active += 1
        self.peak_active = max(self.peak_active, self.active)
        try:
            # Yield once so the scheduler's bounded concurrency can be observed.
            await asyncio.sleep(0)
            if agent_name == DIRECTION_AGENT_NAME:
                parent = input_payload["level1_categories"][0]["category_id"]
                records = tuple(
                    DirectionRecord(
                        analysis_unit_id=unit["analysis_unit_id"],
                        status=DirectionStatus.AVAILABLE,
                        evidence_sufficient=True,
                        technical_problem="目标系统状态不稳定",
                        solution_mechanism="读取状态数据并通过反馈控制调整执行参数",
                        technical_object="目标系统",
                        application_scenarios=("合成压力测试",),
                        direction_summary="基于状态反馈的参数控制方法",
                        keywords=("状态反馈", "参数控制"),
                        candidate_level1_ids=(parent,),
                        confidence=0.9,
                        evidence_ids=tuple(unit["evidence_ids"]),
                    )
                    for unit in input_payload["units"]
                )
                return SimpleNamespace(output=DirectionBatchOutput(records=records))
            if agent_name == CLASSIFICATION_AGENT_NAME:
                decisions = tuple(
                    ClassificationDecision(
                        analysis_unit_id=unit["direction"]["analysis_unit_id"],
                        action=ClassificationAction.EXACT_CATEGORY,
                        primary_category_id=unit["candidate_categories"][0]["category_id"],
                        evidence_ids=tuple(unit["direction"]["evidence_ids"]),
                        confidence=0.9,
                    )
                    for unit in input_payload["units"]
                )
                return SimpleNamespace(
                    output=ClassificationBatchOutput(decisions=decisions)
                )
            raise AssertionError(f"unexpected pressure-test agent: {agent_name}")
        finally:
            self.active -= 1


def pressure_profile():
    return make_profile(
        model_role="bulk",
        verified_context_tokens=600_000,
        safety_ratio=0.65,
        fixed_prompt_tokens=20_000,
        reserved_output_tokens=8_192,
        max_batch_input_tokens=400_000,
        max_batch_output_tokens=8_192,
        max_batch_items=16,
        max_taxonomy_candidates=500,
    )


def packets(case: dict) -> tuple[DirectionUnitPacket, ...]:
    return tuple(
        DirectionUnitPacket(
            analysis_unit_id=f"AU-{index:016x}",
            patent_abstracts=(publication["abstract"],),
            evidence_ids=(f"EV-{index:016x}",),
        )
        for index, publication in enumerate(case["publications"], start=1)
    )


async def execute_case(case_name: str, mode: str):
    case = make_scale_case(case_name, mode=mode)
    model = DeterministicPressureModel()
    scheduler = ModelScheduler(
        ModelBudget(
            max_concurrency=8,
            rpm=100_000,
            tpm=1_000_000_000,
            max_input_tokens=10_000_000,
            max_output_tokens=100_000,
        )
    )
    profile = pressure_profile()
    taxonomy = compile_taxonomy_file(
        ROOT / "development" / "landscape" / "classify.md"
    )
    extracted = await DirectionExtractionService(
        model, scheduler, profile, batch_retries=1
    ).extract(packets(case), taxonomy)
    classified = await ClassificationMatchingService(
        model, scheduler, profile, batch_retries=1
    ).classify(extracted, taxonomy)
    partition = reconcile_terminals(
        tuple(item.analysis_unit_id for item in extracted), classified
    )
    return case, model, extracted, classified, partition


class LandscapeV4PressureTests(unittest.TestCase):
    def test_land_500_completes_three_consecutive_modes(self) -> None:
        for mode in INPUT_MODES:
            with self.subTest(mode=mode):
                started = time.perf_counter()
                case, model, extracted, classified, partition = asyncio.run(
                    execute_case("LAND-500", mode)
                )
                self.assertEqual(len(extracted), 500)
                self.assertEqual(len(classified), 500)
                self.assertEqual(
                    len(partition[ClassificationTerminal.CLASSIFIED]), 500
                )
                self.assertEqual(
                    sum(len(values) for values in partition.values()), 500
                )
                self.assertGreater(model.calls, 2)
                self.assertLessEqual(model.peak_active, 8)
                self.assertEqual(case["mode"], mode)
                self.assertLess(time.perf_counter() - started, 20.0)

    def test_land_1000_completes_with_bounded_memory_and_concurrency(self) -> None:
        started = time.perf_counter()
        case, model, extracted, classified, partition = asyncio.run(
            execute_case("LAND-1000", "COMPANY_AND_TECHNOLOGY")
        )
        elapsed = time.perf_counter() - started
        peak_kib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

        self.assertEqual(len(case["publications"]), 1000)
        self.assertEqual(len(extracted), 1000)
        self.assertEqual(len(classified), 1000)
        self.assertEqual(
            len(partition[ClassificationTerminal.CLASSIFIED]), 1000
        )
        self.assertEqual(sum(len(values) for values in partition.values()), 1000)
        self.assertGreater(model.calls, 4)
        self.assertEqual(model.calls, sum(model.agent_calls.values()))
        self.assertLessEqual(model.agent_calls[DIRECTION_AGENT_NAME], 100)
        self.assertLessEqual(model.agent_calls[CLASSIFICATION_AGENT_NAME], 70)
        self.assertGreater(model.peak_active, 1)
        self.assertLessEqual(model.peak_active, 8)
        self.assertLess(elapsed, 40.0)
        self.assertLess(peak_kib, 192 * 1024)


if __name__ == "__main__":
    unittest.main()
