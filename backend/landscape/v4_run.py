from __future__ import annotations

import hashlib
import uuid
from datetime import date
from enum import StrEnum

from pydantic import Field, model_validator

from .scope import LandscapeInputMode, ScopeModel


class LandscapeRunStatus(StrEnum):
    PLANNING = "PLANNING"
    ESTIMATING = "ESTIMATING"
    AWAITING_SCALE_CONFIRMATION = "AWAITING_SCALE_CONFIRMATION"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING_FOR_CREDENTIALS = "WAITING_FOR_CREDENTIALS"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_LIMITATIONS = "COMPLETED_WITH_LIMITATIONS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class LandscapeRun(ScopeModel):
    run_id: str = Field(pattern=r"^LRN-[0-9a-f]{16}$")
    scope_revision_id: str = Field(pattern=r"^SCR-[0-9a-f]{16}$")
    scope_revision_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    taxonomy_version: str = Field(min_length=1, max_length=200)
    taxonomy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: LandscapeRunStatus
    mode: LandscapeInputMode
    publication_start: date
    publication_end: date
    workflow_version: str = Field(pattern=r"^landscape-v4/[0-9]+\.[0-9]+\.[0-9]+$")
    created_at: int = Field(ge=0)
    updated_at: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_boundaries(self) -> "LandscapeRun":
        if self.publication_end < self.publication_start:
            raise ValueError("run publication date range is reversed")
        if self.updated_at < self.created_at:
            raise ValueError("run updated_at precedes created_at")
        return self


def make_landscape_run_id(seed: str | None = None) -> str:
    value = seed or uuid.uuid4().hex
    return f"LRN-{hashlib.sha256(value.encode('utf-8')).hexdigest()[:16]}"


__all__ = [
    "LandscapeRun",
    "LandscapeRunStatus",
    "make_landscape_run_id",
]
