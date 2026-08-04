from __future__ import annotations

import unittest

from landscape.batching import BatchItem, BatchPackingError, make_profile, pack_items, split_batch


class LandscapeBatchingTests(unittest.TestCase):
    def setUp(self):
        self.profile = make_profile(
            model_role="bulk", verified_context_tokens=1000, safety_ratio=0.6,
            fixed_prompt_tokens=100, reserved_output_tokens=100,
            max_batch_input_tokens=500, max_batch_output_tokens=400,
            max_batch_items=3, max_taxonomy_candidates=10,
        )

    def test_packing_is_deterministic_and_respects_all_limits(self):
        items = tuple(BatchItem(f"AU-{i}", 100, 80, 2) for i in (3, 1, 2, 4))
        first = pack_items(self.profile, items)
        second = pack_items(self.profile, tuple(reversed(items)))
        self.assertEqual(first, second)
        self.assertEqual([batch.item_ids for batch in first], [("AU-1", "AU-2", "AU-3"), ("AU-4",)])
        self.assertTrue(all(batch.input_tokens <= 500 for batch in first))

    def test_item_that_cannot_fit_fails_closed(self):
        with self.assertRaises(BatchPackingError):
            pack_items(self.profile, (BatchItem("AU-1", 501, 1),))

    def test_split_is_deterministic_and_never_larger(self):
        batch = pack_items(self.profile, tuple(BatchItem(f"AU-{i}", 100, 50) for i in range(3)))[0]
        parts = split_batch(batch, self.profile)
        self.assertEqual([part.item_ids for part in parts], [("AU-0",), ("AU-1", "AU-2")])
        self.assertEqual(sum(len(part.item_ids) for part in parts), len(batch.item_ids))


if __name__ == "__main__": unittest.main()
