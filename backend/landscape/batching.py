from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from pydantic import Field

from .scope import ScopeModel


class BatchPackingError(ValueError):
    pass


class BatchProfile(ScopeModel):
    profile_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_role: str = Field(min_length=1, max_length=50)
    verified_context_tokens: int = Field(ge=1)
    safety_ratio: float = Field(gt=0, le=1)
    fixed_prompt_tokens: int = Field(ge=0)
    reserved_output_tokens: int = Field(ge=0)
    max_batch_input_tokens: int = Field(ge=1)
    max_batch_output_tokens: int = Field(ge=1)
    max_batch_items: int = Field(ge=1, le=100)
    max_taxonomy_candidates: int = Field(ge=1, le=500)


@dataclass(frozen=True)
class BatchItem:
    item_id: str
    input_tokens: int
    output_tokens: int
    taxonomy_candidates: int = 0


class PackedBatch(ScopeModel):
    batch_id: str = Field(pattern=r"^BAT-[0-9a-f]{16}$")
    item_ids: tuple[str, ...] = Field(min_length=1)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    taxonomy_candidates: int = Field(ge=0)
    profile_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


def make_profile(**values) -> BatchProfile:
    payload = dict(values)
    payload["profile_hash"] = _hash({key: value for key, value in payload.items() if key != "profile_hash"})
    return BatchProfile(**payload)


def pack_items(profile: BatchProfile, items: tuple[BatchItem, ...]) -> tuple[PackedBatch, ...]:
    if len({item.item_id for item in items}) != len(items):
        raise BatchPackingError("duplicate batch item")
    usable_input = min(
        profile.max_batch_input_tokens,
        int(profile.verified_context_tokens * profile.safety_ratio)
        - profile.fixed_prompt_tokens
        - profile.reserved_output_tokens,
    )
    if usable_input < 1:
        raise BatchPackingError("batch profile leaves no usable input budget")
    batches: list[list[BatchItem]] = []
    current: list[BatchItem] = []
    input_total = output_total = candidates = 0
    for item in sorted(items, key=lambda value: value.item_id):
        if min(item.input_tokens, item.output_tokens, item.taxonomy_candidates) < 0:
            raise BatchPackingError("batch item token counts must be non-negative")
        if item.input_tokens > usable_input or item.output_tokens > profile.max_batch_output_tokens or item.taxonomy_candidates > profile.max_taxonomy_candidates:
            raise BatchPackingError(f"item cannot fit batch profile: {item.item_id}")
        would_exceed = (
            current and (
                len(current) >= profile.max_batch_items
                or input_total + item.input_tokens > usable_input
                or output_total + item.output_tokens > profile.max_batch_output_tokens
                or candidates + item.taxonomy_candidates > profile.max_taxonomy_candidates
            )
        )
        if would_exceed:
            batches.append(current)
            current = []
            input_total = output_total = candidates = 0
        current.append(item)
        input_total += item.input_tokens
        output_total += item.output_tokens
        candidates += item.taxonomy_candidates
    if current: batches.append(current)
    return tuple(_make_batch(profile, batch) for batch in batches)


def split_batch(batch: PackedBatch, profile: BatchProfile) -> tuple[PackedBatch, ...]:
    if len(batch.item_ids) <= 1:
        return (batch,)
    midpoint = len(batch.item_ids) // 2
    # Splitting is deterministic by the already frozen item order; it never
    # creates a larger batch or changes member order.
    parts = (batch.item_ids[:midpoint], batch.item_ids[midpoint:])
    return tuple(
        PackedBatch(
            batch_id=f"BAT-{_hash((profile.profile_hash, part))[:16]}",
            item_ids=part,
            input_tokens=0,
            output_tokens=0,
            taxonomy_candidates=0,
            profile_hash=profile.profile_hash,
        )
        for part in parts
    )


def _make_batch(profile: BatchProfile, values: list[BatchItem]) -> PackedBatch:
    ids = tuple(item.item_id for item in values)
    return PackedBatch(
        batch_id=f"BAT-{_hash((profile.profile_hash, ids))[:16]}",
        item_ids=ids,
        input_tokens=sum(item.input_tokens for item in values),
        output_tokens=sum(item.output_tokens for item in values),
        taxonomy_candidates=sum(item.taxonomy_candidates for item in values),
        profile_hash=profile.profile_hash,
    )


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


__all__ = ["BatchItem", "BatchPackingError", "BatchProfile", "PackedBatch", "make_profile", "pack_items", "split_batch"]
