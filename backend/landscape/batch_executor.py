from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable

from .batching import BatchProfile, PackedBatch


class BatchValidationError(RuntimeError):
    def __init__(self, message: str, *, partial: dict[str, object] | None = None):
        super().__init__(message)
        self.partial = partial or {}


class BatchExecutionResult:
    def __init__(self, successes: dict[str, object], unresolved: tuple[str, ...]):
        self.successes = successes
        self.unresolved = unresolved


class ResilientBatchExecutor:
    def __init__(self, profile: BatchProfile, *, batch_retries: int = 1):
        if batch_retries < 0: raise ValueError("batch_retries must be non-negative")
        self.profile = profile
        self.batch_retries = batch_retries

    async def execute(
        self,
        batch: PackedBatch,
        operation: Callable[[tuple[str, ...]], Awaitable[dict[str, object]]],
    ) -> BatchExecutionResult:
        successes: dict[str, object] = {}
        unresolved: list[str] = []
        await self._run(batch, operation, successes, unresolved)
        return BatchExecutionResult(successes, tuple(sorted(unresolved)))

    async def _run(self, batch, operation, successes, unresolved):
        remaining = tuple(item_id for item_id in batch.item_ids if item_id not in successes)
        if not remaining: return
        current = PackedBatch(
            batch_id=batch.batch_id, item_ids=remaining, input_tokens=batch.input_tokens,
            output_tokens=batch.output_tokens, taxonomy_candidates=batch.taxonomy_candidates,
            profile_hash=batch.profile_hash,
        )
        partial: dict[str, object] = {}
        succeeded = False
        for attempt in range(self.batch_retries + 1):
            try:
                response = await operation(current.item_ids)
                if set(response) != set(current.item_ids):
                    raise BatchValidationError("batch response members do not match request", partial=response)
                partial = response
                succeeded = True
                break
            except BatchValidationError as exc:
                partial.update({key: value for key, value in exc.partial.items() if key in current.item_ids})
            except Exception:
                pass
        for key in tuple(partial):
            if key in current.item_ids:
                successes[key] = partial[key]
        missing = tuple(key for key in current.item_ids if key not in successes)
        if not missing: return
        if len(missing) == 1:
            unresolved.append(missing[0])
            return
        # The failure isolation boundary is deterministic and only shrinks.
        midpoint = len(missing) // 2
        children = (missing[:midpoint], missing[midpoint:])
        for child_ids in children:
            child = PackedBatch(
                batch_id=f"BAT-{hashlib.sha256((batch.batch_id + '|' + '|'.join(child_ids)).encode()).hexdigest()[:16]}", item_ids=child_ids,
                input_tokens=0, output_tokens=0, taxonomy_candidates=0,
                profile_hash=batch.profile_hash,
            )
            await self._run(child, operation, successes, unresolved)


__all__ = ["BatchExecutionResult", "BatchValidationError", "ResilientBatchExecutor"]
