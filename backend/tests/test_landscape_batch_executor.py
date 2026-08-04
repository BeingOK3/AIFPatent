from __future__ import annotations

import asyncio
import unittest

from landscape.batch_executor import BatchValidationError, ResilientBatchExecutor
from landscape.batching import BatchItem, make_profile, pack_items


class LandscapeBatchExecutorTests(unittest.TestCase):
    def setUp(self):
        profile = make_profile(model_role="bulk", verified_context_tokens=1000, safety_ratio=.8, fixed_prompt_tokens=10, reserved_output_tokens=10, max_batch_input_tokens=500, max_batch_output_tokens=400, max_batch_items=10, max_taxonomy_candidates=20)
        self.batch = pack_items(profile, tuple(BatchItem(f"AU-{i}", 20, 10) for i in range(4)))[0]
        self.executor = ResilientBatchExecutor(profile)

    def test_failed_batch_is_split_and_successful_children_are_kept(self):
        calls=[]
        async def operation(ids):
            calls.append(ids)
            if len(ids) > 1: raise RuntimeError("batch failure")
            return {ids[0]: {"ok": True}}
        result = asyncio.run(self.executor.execute(self.batch, operation))
        self.assertEqual(set(result.successes), set(self.batch.item_ids))
        self.assertEqual(result.unresolved, ())
        self.assertEqual(calls[0], self.batch.item_ids)
        self.assertEqual(sum(len(ids) == 1 for ids in calls), 4)
        self.assertTrue(all(len(ids) <= len(self.batch.item_ids) for ids in calls))

    def test_partial_invalid_response_is_isolated_to_missing_items(self):
        async def operation(ids):
            if len(ids) > 1:
                raise BatchValidationError("partial", partial={ids[0]: "valid"})
            return {ids[0]: "valid"} if ids[0] != "AU-3" else {}
        result = asyncio.run(self.executor.execute(self.batch, operation))
        self.assertEqual(set(result.successes), {"AU-0", "AU-1", "AU-2"})
        self.assertEqual(result.unresolved, ("AU-3",))


if __name__ == "__main__": unittest.main()
