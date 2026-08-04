from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from landscape.batching import make_profile
from landscape.classification_agent import (
    _candidate_leaves,
    _leaf_parent_pairs,
    ClassificationBatchOutput,
    ClassificationDecision,
    ClassificationMatchingService,
)
from landscape.classification_terminal import (
    ClassificationAction,
    ClassificationTerminal,
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


def direction(index, *, unresolved=False):
    return DirectionRecord(
        analysis_unit_id=f"AU-{index:016x}",
        status=DirectionStatus.UNRESOLVED if unresolved else DirectionStatus.AVAILABLE,
        evidence_sufficient=not unresolved,
        solution_mechanism="" if unresolved else "通过液体回路散热",
        direction_summary="" if unresolved else "冷板液冷技术方向",
        candidate_level1_ids=() if unresolved else (TAXONOMY.nodes[0].category_id,),
        confidence=0 if unresolved else 0.8,
        evidence_ids=() if unresolved else (f"EV-{index}",),
        unresolved_reason="ABSTRACT_MISSING" if unresolved else None,
    )


class FakeModel:
    def __init__(self):
        self.calls = []

    async def complete(self, agent_name, *, system_prompt, input_payload):
        round_number = input_payload["review_round"]
        self.calls.append((round_number, input_payload))
        decisions = []
        for unit in input_payload["units"]:
            item = unit["direction"]
            candidates = unit["candidate_categories"]
            if item["analysis_unit_id"].endswith("0001") and round_number == 0:
                decisions.append(
                    ClassificationDecision(
                        analysis_unit_id=item["analysis_unit_id"],
                        action=ClassificationAction.NEEDS_ALTERNATIVE_PARENT,
                        confidence=0.6,
                        evidence_ids=(item["evidence_ids"][0],),
                    )
                )
            else:
                decisions.append(
                    ClassificationDecision(
                        analysis_unit_id=item["analysis_unit_id"],
                        action=ClassificationAction.EXACT_CATEGORY,
                        primary_category_id=candidates[0]["category_id"],
                        confidence=0.85,
                        evidence_ids=(item["evidence_ids"][0],),
                    )
                )
        return SimpleNamespace(output=ClassificationBatchOutput(decisions=tuple(decisions)))


def service(model):
    profile = make_profile(
        model_role="bulk",
        verified_context_tokens=100_000,
        safety_ratio=0.8,
        fixed_prompt_tokens=100,
        reserved_output_tokens=100,
        max_batch_input_tokens=20_000,
        max_batch_output_tokens=2_000,
        max_batch_items=4,
        max_taxonomy_candidates=20,
    )
    scheduler = ModelScheduler(
        ModelBudget(2, 100, 100_000, 30_000, 5_000)
    )
    return ClassificationMatchingService(model, scheduler, profile, batch_retries=0)


class ClassificationMatchingServiceTests(unittest.TestCase):
    def test_alternative_parent_triggers_one_reflection_round(self):
        model = FakeModel()
        results = asyncio.run(service(model).classify((direction(0), direction(1)), TAXONOMY))
        by_id = {item.analysis_unit_id: item for item in results}
        self.assertEqual(by_id[direction(0).analysis_unit_id].review_round, 0)
        reflected = by_id[direction(1).analysis_unit_id]
        self.assertEqual(reflected.review_round, 1)
        self.assertEqual(reflected.terminal, ClassificationTerminal.CLASSIFIED)
        self.assertEqual([call[0] for call in model.calls], [0, 1])
        first_ids = {
            item["category_id"] for item in model.calls[0][1]["units"][1]["candidate_categories"]
        }
        second_ids = {
            item["category_id"] for item in model.calls[1][1]["units"][0]["candidate_categories"]
        }
        self.assertTrue(first_ids.isdisjoint(second_ids))

    def test_unresolved_direction_never_calls_model(self):
        model = FakeModel()
        results = asyncio.run(service(model).classify((direction(2, unresolved=True),), TAXONOMY))
        self.assertEqual(results[0].terminal, ClassificationTerminal.UNRESOLVED)
        self.assertEqual(results[0].unresolved_reason, "ABSTRACT_MISSING")
        self.assertEqual(model.calls, [])

    def test_candidate_leaves_are_scoped_deterministic_and_complete(self):
        taxonomy = compile_taxonomy_markdown(
            """| 一级分类 | 二级分类 | 三级分类 |
| --- | --- | --- |
| Alpha | One | LeafA |
| Alpha | Two | LeafB |
| Beta | Three | LeafC |
"""
        )
        node_by_path = {node.path: node for node in taxonomy.nodes}
        alpha_id = node_by_path[("Alpha",)].category_id
        beta_id = node_by_path[("Beta",)].category_id
        leaf_ids = tuple(node.category_id for node in taxonomy.nodes if node.is_leaf)

        alpha_only = DirectionRecord(
            analysis_unit_id="AU-00000000000000aa",
            status=DirectionStatus.AVAILABLE,
            evidence_sufficient=True,
            solution_mechanism="通过液体回路散热",
            direction_summary="冷板液冷技术方向",
            candidate_level1_ids=(alpha_id,),
            confidence=0.8,
            evidence_ids=("EV-aa",),
        )
        pairs = _leaf_parent_pairs(taxonomy)
        scoped = _candidate_leaves(alpha_only, taxonomy, pairs)
        self.assertEqual(
            set(scoped),
            {node_by_path[("Alpha", "One", "LeafA")].category_id,
             node_by_path[("Alpha", "Two", "LeafB")].category_id},
        )
        self.assertEqual(scoped, tuple(leaf_id for leaf_id in leaf_ids if leaf_id in set(scoped)))

        alpha_only_again = DirectionRecord(
            analysis_unit_id="AU-00000000000000bb",
            status=DirectionStatus.AVAILABLE,
            evidence_sufficient=True,
            solution_mechanism="通过液体回路散热",
            direction_summary="冷板液冷技术方向",
            candidate_level1_ids=(alpha_id,),
            confidence=0.8,
            evidence_ids=("EV-bb",),
        )
        self.assertEqual(_candidate_leaves(alpha_only_again, taxonomy, pairs), scoped)

        unscoped = DirectionRecord(
            analysis_unit_id="AU-00000000000000cc",
            status=DirectionStatus.AVAILABLE,
            evidence_sufficient=True,
            solution_mechanism="通过液体回路散热",
            direction_summary="冷板液冷技术方向",
            candidate_level1_ids=(),
            confidence=0.8,
            evidence_ids=("EV-cc",),
        )
        self.assertEqual(_candidate_leaves(unscoped, taxonomy, pairs), leaf_ids)


if __name__ == "__main__":
    unittest.main()
