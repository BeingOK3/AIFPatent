from __future__ import annotations

import calendar
import hashlib
import json
from collections import defaultdict
from datetime import date, timedelta
from enum import StrEnum

from pydantic import Field, model_validator

from .scope import ScopeModel


METRIC_POLICY_VERSION = "landscape-metrics/1"


class TimeBucketGranularity(StrEnum):
    MONTH = "MONTH"
    QUARTER = "QUARTER"
    YEAR = "YEAR"


class OrganizationCountingMode(StrEnum):
    PRIMARY = "PRIMARY"
    ALL_KNOWN = "ALL_KNOWN"


class MetricPublication(ScopeModel):
    publication_id: str = Field(min_length=1, max_length=100)
    publication_number: str = Field(min_length=1, max_length=100)
    title: str = Field(default="", max_length=2000)
    publication_date: date
    primary_organization_id: str = Field(min_length=1, max_length=300)
    co_organization_ids: tuple[str, ...] = Field(default=(), max_length=50)

    @model_validator(mode="after")
    def validate_organizations(self) -> "MetricPublication":
        if len(self.co_organization_ids) != len(set(self.co_organization_ids)):
            raise ValueError("duplicate co-organization")
        if self.primary_organization_id in self.co_organization_ids:
            raise ValueError("primary organization cannot also be co-organization")
        return self


class MetricAnalysisUnit(ScopeModel):
    analysis_unit_id: str = Field(pattern=r"^AU-[0-9a-f]{16}$")
    direction_id: str = Field(min_length=1, max_length=300)
    classification_path: tuple[str, ...] = Field(default=(), max_length=20)
    publications: tuple[MetricPublication, ...] = Field(min_length=1, max_length=100)
    classification_confidence: float = Field(ge=0, le=1)
    evidence_completeness: float = Field(ge=0, le=1)
    direction_centrality: float = Field(default=0, ge=0, le=1)
    evidence_ids: tuple[str, ...] = Field(default=(), max_length=100)

    @model_validator(mode="after")
    def validate_publications(self) -> "MetricAnalysisUnit":
        publication_ids = [item.publication_id for item in self.publications]
        if len(publication_ids) != len(set(publication_ids)):
            raise ValueError("duplicate publication in analysis unit")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("duplicate metric evidence")
        return self


class TimeBucket(ScopeModel):
    bucket_id: str = Field(pattern=r"^TB-[0-9a-f]{16}$")
    label: str = Field(min_length=1, max_length=100)
    start: date
    end: date
    granularity: TimeBucketGranularity

    @model_validator(mode="after")
    def validate_interval(self) -> "TimeBucket":
        if self.end < self.start:
            raise ValueError("time bucket interval is invalid")
        return self


class MetricCell(ScopeModel):
    direction_id: str
    organization_id: str
    bucket_id: str
    analysis_unit_ids: tuple[str, ...]
    publication_ids: tuple[str, ...]
    analysis_unit_count: int = Field(ge=0)
    publication_count: int = Field(ge=0)
    direction_share: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_counts(self) -> "MetricCell":
        if self.analysis_unit_count != len(self.analysis_unit_ids):
            raise ValueError("analysis unit count mismatch")
        if self.publication_count != len(self.publication_ids):
            raise ValueError("publication count mismatch")
        if tuple(sorted(set(self.analysis_unit_ids))) != self.analysis_unit_ids:
            raise ValueError("analysis unit IDs must be unique and sorted")
        if tuple(sorted(set(self.publication_ids))) != self.publication_ids:
            raise ValueError("publication IDs must be unique and sorted")
        return self


class MetricCube(ScopeModel):
    policy_version: str = METRIC_POLICY_VERSION
    publication_start: date
    publication_end: date
    organization_counting_mode: OrganizationCountingMode
    analysis_unit_time_policy: str = "EARLIEST_PUBLICATION_IN_FAMILY"
    buckets: tuple[TimeBucket, ...]
    cells: tuple[MetricCell, ...]
    analysis_unit_count: int = Field(ge=0)
    publication_count: int = Field(ge=0)
    excluded_out_of_range_publication_count: int = Field(ge=0)
    cube_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_hash(self) -> "MetricCube":
        semantic = self.model_dump(mode="json", exclude={"cube_hash"})
        if _hash(semantic) != self.cube_hash:
            raise ValueError("metric cube hash mismatch")
        return self


def build_metric_cube(
    units: tuple[MetricAnalysisUnit, ...],
    *,
    publication_start: date,
    publication_end: date,
    organization_counting_mode: OrganizationCountingMode = OrganizationCountingMode.PRIMARY,
) -> MetricCube:
    if publication_end < publication_start:
        raise ValueError("publication_end must not precede publication_start")
    unit_ids = [unit.analysis_unit_id for unit in units]
    if len(unit_ids) != len(set(unit_ids)):
        raise ValueError("duplicate metric analysis unit")
    all_publication_ids = [
        publication.publication_id for unit in units for publication in unit.publications
    ]
    if len(all_publication_ids) != len(set(all_publication_ids)):
        raise ValueError("publication belongs to more than one analysis unit")
    buckets = make_time_buckets(publication_start, publication_end)
    bucket_by_date = {
        current: bucket
        for bucket in buckets
        for current in _dates(bucket.start, bucket.end)
    }
    cell_units: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    cell_publications: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    in_range_publications = 0
    excluded = 0
    for unit in units:
        in_range = tuple(
            publication
            for publication in unit.publications
            if publication_start <= publication.publication_date <= publication_end
        )
        excluded += len(unit.publications) - len(in_range)
        if not in_range:
            continue
        in_range_publications += len(in_range)
        anchor = min(in_range, key=lambda item: (item.publication_date, item.publication_id))
        anchor_bucket = bucket_by_date[anchor.publication_date]
        unit_organizations = sorted(
            {
                organization_id
                for publication in in_range
                for organization_id in _organizations(
                    publication,
                    organization_counting_mode,
                )
            }
        )
        for organization_id in unit_organizations:
            cell_units[(unit.direction_id, organization_id, anchor_bucket.bucket_id)].add(
                unit.analysis_unit_id
            )
        for publication in in_range:
            bucket = bucket_by_date[publication.publication_date]
            for organization_id in _organizations(publication, organization_counting_mode):
                cell_publications[(unit.direction_id, organization_id, bucket.bucket_id)].add(
                    publication.publication_id
                )
    keys = sorted(set(cell_units) | set(cell_publications))
    total_units_by_organization_bucket: dict[tuple[str, str], set[str]] = defaultdict(set)
    for (direction_id, organization_id, bucket_id), members in cell_units.items():
        total_units_by_organization_bucket[(organization_id, bucket_id)].update(members)
    cells = []
    for direction_id, organization_id, bucket_id in keys:
        analysis_unit_ids = tuple(
            sorted(cell_units[(direction_id, organization_id, bucket_id)])
        )
        publication_ids = tuple(
            sorted(cell_publications[(direction_id, organization_id, bucket_id)])
        )
        denominator = len(total_units_by_organization_bucket[(organization_id, bucket_id)])
        cells.append(
            MetricCell(
                direction_id=direction_id,
                organization_id=organization_id,
                bucket_id=bucket_id,
                analysis_unit_ids=analysis_unit_ids,
                publication_ids=publication_ids,
                analysis_unit_count=len(analysis_unit_ids),
                publication_count=len(publication_ids),
                direction_share=round(len(analysis_unit_ids) / denominator, 8)
                if denominator
                else 0,
            )
        )
    semantic = {
        "policy_version": METRIC_POLICY_VERSION,
        "publication_start": publication_start.isoformat(),
        "publication_end": publication_end.isoformat(),
        "organization_counting_mode": organization_counting_mode.value,
        "analysis_unit_time_policy": "EARLIEST_PUBLICATION_IN_FAMILY",
        "buckets": [bucket.model_dump(mode="json") for bucket in buckets],
        "cells": [cell.model_dump(mode="json") for cell in cells],
        "analysis_unit_count": sum(
            1
            for unit in units
            if any(
                publication_start <= publication.publication_date <= publication_end
                for publication in unit.publications
            )
        ),
        "publication_count": in_range_publications,
        "excluded_out_of_range_publication_count": excluded,
    }
    return MetricCube(**semantic, cube_hash=_hash(semantic))


def make_time_buckets(publication_start: date, publication_end: date) -> tuple[TimeBucket, ...]:
    if publication_end < publication_start:
        raise ValueError("publication_end must not precede publication_start")
    granularity = _auto_granularity(publication_start, publication_end)
    buckets = []
    current = publication_start
    while current <= publication_end:
        natural_end = _natural_bucket_end(current, granularity)
        end = min(natural_end, publication_end)
        label = _bucket_label(current, granularity)
        identity = f"{granularity.value}|{current.isoformat()}|{end.isoformat()}"
        buckets.append(
            TimeBucket(
                bucket_id=f"TB-{hashlib.sha256(identity.encode()).hexdigest()[:16]}",
                label=label,
                start=current,
                end=end,
                granularity=granularity,
            )
        )
        current = end + timedelta(days=1)
    return tuple(buckets)


def _auto_granularity(start: date, end: date) -> TimeBucketGranularity:
    if end < _add_months(start, 6):
        return TimeBucketGranularity.MONTH
    if end < _add_months(start, 24):
        return TimeBucketGranularity.QUARTER
    return TimeBucketGranularity.YEAR


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _natural_bucket_end(start: date, granularity: TimeBucketGranularity) -> date:
    if granularity == TimeBucketGranularity.MONTH:
        return date(start.year, start.month, calendar.monthrange(start.year, start.month)[1])
    if granularity == TimeBucketGranularity.QUARTER:
        last_month = ((start.month - 1) // 3 + 1) * 3
        return date(start.year, last_month, calendar.monthrange(start.year, last_month)[1])
    return date(start.year, 12, 31)


def _bucket_label(start: date, granularity: TimeBucketGranularity) -> str:
    if granularity == TimeBucketGranularity.MONTH:
        return f"{start.year:04d}-{start.month:02d}"
    if granularity == TimeBucketGranularity.QUARTER:
        return f"{start.year:04d}-Q{(start.month - 1) // 3 + 1}"
    return f"{start.year:04d}"


def _organizations(
    publication: MetricPublication,
    mode: OrganizationCountingMode,
) -> tuple[str, ...]:
    if mode == OrganizationCountingMode.PRIMARY:
        return (publication.primary_organization_id,)
    return tuple(sorted({publication.primary_organization_id, *publication.co_organization_ids}))


def _dates(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


__all__ = [
    "METRIC_POLICY_VERSION",
    "MetricAnalysisUnit",
    "MetricCell",
    "MetricCube",
    "MetricPublication",
    "OrganizationCountingMode",
    "TimeBucket",
    "TimeBucketGranularity",
    "build_metric_cube",
    "make_time_buckets",
]
