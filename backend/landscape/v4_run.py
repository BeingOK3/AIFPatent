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
    started_at: int | None = Field(default=None, ge=0)
    completed_at: int | None = Field(default=None, ge=0)
    error_code: str | None = Field(default=None, max_length=200)
    error_message: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_boundaries(self) -> "LandscapeRun":
        if self.publication_end < self.publication_start:
            raise ValueError("run publication date range is reversed")
        if self.updated_at < self.created_at:
            raise ValueError("run updated_at precedes created_at")
        if self.started_at is not None and self.started_at < self.created_at:
            raise ValueError("run started_at precedes created_at")
        if self.completed_at is not None and self.started_at is not None and self.completed_at < self.started_at:
            raise ValueError("run completed_at precedes started_at")
        if self.status == LandscapeRunStatus.FAILED and not self.error_code:
            raise ValueError("failed run requires error code")
        if self.status != LandscapeRunStatus.FAILED and (self.error_code or self.error_message):
            raise ValueError("only failed run may contain an error")
        return self


def make_landscape_run_id(seed: str | None = None) -> str:
    value = seed or uuid.uuid4().hex
    return f"LRN-{hashlib.sha256(value.encode('utf-8')).hexdigest()[:16]}"


__all__ = [
    "LandscapeRun",
    "LandscapeRunStatus",
    "make_landscape_run_id",
]
