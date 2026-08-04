from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass


class ModelBudgetError(ValueError):
    pass


@dataclass(frozen=True)
class ModelBudget:
    max_concurrency: int
    rpm: int
    tpm: int
    max_input_tokens: int
    max_output_tokens: int

    def __post_init__(self):
        if min(self.max_concurrency, self.rpm, self.tpm, self.max_input_tokens, self.max_output_tokens) < 1:
            raise ModelBudgetError("model budgets must be positive")


class SlidingWindowBudget:
    def __init__(self, budget: ModelBudget):
        self.budget = budget
        self._requests: deque[tuple[float, int]] = deque()
        self._lock = asyncio.Lock()

    async def reserve(self, input_tokens: int, output_tokens: int, *, now: float | None = None) -> float:
        if not 0 <= input_tokens <= self.budget.max_input_tokens:
            raise ModelBudgetError("input token request exceeds model budget")
        if not 0 <= output_tokens <= self.budget.max_output_tokens:
            raise ModelBudgetError("output token request exceeds model budget")
        total = input_tokens + output_tokens
        if total > self.budget.tpm:
            raise ModelBudgetError("request exceeds TPM budget")
        current = time.monotonic() if now is None else now
        async with self._lock:
            self._prune(current)
            if len(self._requests) >= self.budget.rpm:
                return max(0.0, 60.0 - (current - self._requests[0][0]))
            used = sum(tokens for _timestamp, tokens in self._requests)
            if used + total > self.budget.tpm:
                return max(0.0, 60.0 - (current - self._requests[0][0]))
            self._requests.append((current, total))
            return 0.0

    async def wait_and_reserve(self, input_tokens: int, output_tokens: int) -> None:
        while True:
            wait_seconds = await self.reserve(input_tokens, output_tokens)
            if wait_seconds <= 0: return
            await asyncio.sleep(min(wait_seconds, 60.0))

    def _prune(self, now: float) -> None:
        while self._requests and now - self._requests[0][0] >= 60.0:
            self._requests.popleft()


class ModelScheduler:
    def __init__(self, budget: ModelBudget):
        self.budget = budget
        self.window = SlidingWindowBudget(budget)
        self._semaphore = asyncio.Semaphore(budget.max_concurrency)

    async def run(self, input_tokens: int, output_tokens: int, operation):
        async with self._semaphore:
            await self.window.wait_and_reserve(input_tokens, output_tokens)
            return await operation()


__all__ = ["ModelBudget", "ModelBudgetError", "ModelScheduler", "SlidingWindowBudget"]
