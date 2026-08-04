from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from landscape.batching import make_profile
from landscape.direction_agent import (
    DirectionBatchOutput,
    DirectionExtractionService,
    DirectionUnitPacket,
)
from landscape.direction_record import DirectionRecord, DirectionStatus
from landscape.model_scheduler import ModelBudget, ModelScheduler
from landscape.taxonomy import compile_taxonomy_markdown


TAXONOMY = compile_taxonomy_markdown(
    """| 一级分类 | 二级分类 | 三级分类 |
| --- | --- | --- |
| Hardware | Cooling | Liquid |
| Software | Scheduling | Dynamic |
"""
)


class FakeModel:
    def __init__(self, *, fail_unit=None):
        self.fail_unit = fail_unit
        self.active = 0
        self.peak = 0
        self.calls = []

    async def complete(self, agent_name, *, system_prompt, input_payload):
        ids = tuple(item["analysis_unit_id"] for item in input_payload["units"])
        self.calls.append(ids)
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        if self.fail_unit in ids:
            raise RuntimeError("model failure")
        level1 = input_payload["level1_categories"][0]["category_id"]
        return SimpleNamespace(
            output=DirectionBatchOutput(
                records=tuple(
                    DirectionRecord(
                        analysis_unit_id=item["analysis_unit_id"],
                        status=DirectionStatus.AVAILABLE,
                        evidence_sufficient=True,
                        solution_mechanism="通过液体回路带走热量",
                        direction_summary="该方案利用冷板液冷回路降低器件温度。",
                        keywords=("液冷", "冷板"),
                        candidate_level1_ids=(level1,),
                        confidence=0.8,
                        evidence_ids=(item["evidence_ids"][0],),
                    )
                    for item in input_payload["units"]
                )
            )
        )


def packet(index):
    return DirectionUnitPacket(
        analysis_unit_id=f"AU-{index:016x}",
        patent_abstracts=(f"专利 {index} 通过冷板和液体回路对处理器进行散热。",),
        evidence_ids=(f"EV-{index}",),
    )


def service(model):
    profile = make_profile(
        model_role="bulk",
        verified_context_tokens=100_000,
        safety_ratio=0.8,
        fixed_prompt_tokens=100,
        reserved_output_tokens=100,
        max_batch_input_tokens=20_000,
        max_batch_output_tokens=2_000,
        max_batch_items=2,
        max_taxonomy_candidates=20,
    )
    scheduler = ModelScheduler(
        ModelBudget(
            max_concurrency=2,
            rpm=100,
            tpm=100_000,
            max_input_tokens=30_000,
            max_output_tokens=5_000,
        )
    )
    return DirectionExtractionService(model, scheduler, profile, batch_retries=0)


class DirectionExtractionServiceTests(unittest.TestCase):
    def test_multiple_batches_call_model_concurrently_with_bounded_parallelism(self):
        model = FakeModel()
        records = asyncio.run(
            service(model).extract(tuple(packet(index) for index in range(4)), TAXONOMY)
        )
        self.assertEqual(len(records), 4)
        self.assertEqual(model.peak, 2)
        self.assertEqual(len(model.calls), 2)
        self.assertTrue(all(record.status == DirectionStatus.AVAILABLE for record in records))

    def test_failed_batch_isolated_to_terminal_unresolved_units(self):
        failing = packet(1).analysis_unit_id
        model = FakeModel(fail_unit=failing)
        records = asyncio.run(
            service(model).extract((packet(0), packet(1), packet(2)), TAXONOMY)
        )
        by_id = {record.analysis_unit_id: record for record in records}
        self.assertEqual(by_id[failing].status, DirectionStatus.UNRESOLVED)
        self.assertEqual(by_id[failing].unresolved_reason, "MODEL_BATCH_FAILED")
        self.assertEqual(by_id[packet(0).analysis_unit_id].status, DirectionStatus.AVAILABLE)
        self.assertEqual(by_id[packet(2).analysis_unit_id].status, DirectionStatus.AVAILABLE)


if __name__ == "__main__":
    unittest.main()
