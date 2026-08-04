from __future__ import annotations

import asyncio
import unittest

from landscape.model_scheduler import ModelBudget, ModelBudgetError, SlidingWindowBudget


class LandscapeModelSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.window = SlidingWindowBudget(ModelBudget(2, rpm=2, tpm=100, max_input_tokens=80, max_output_tokens=50))

    def test_rpm_and_tpm_return_bounded_wait_without_reserving(self):
        async def scenario():
            self.assertEqual(await self.window.reserve(30, 20, now=0), 0)
            self.assertEqual(await self.window.reserve(30, 20, now=1), 0)
            rpm_wait = await self.window.reserve(1, 1, now=2)
            self.assertGreater(rpm_wait, 0)
            return await self.window.reserve(1, 1, now=61)
        self.assertEqual(asyncio.run(scenario()), 0)

    def test_single_request_hard_limits_fail_closed(self):
        async def scenario():
            with self.assertRaises(ModelBudgetError): await self.window.reserve(81, 1)
            with self.assertRaises(ModelBudgetError): await self.window.reserve(1, 51)
            with self.assertRaises(ModelBudgetError): await self.window.reserve(60, 50)
        asyncio.run(scenario())


if __name__ == "__main__": unittest.main()
