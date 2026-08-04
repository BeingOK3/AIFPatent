from __future__ import annotations

from collections import defaultdict
from enum import StrEnum

from pydantic import Field, model_validator

from .metrics import MetricCube
from .scope import LandscapeInputMode, ScopeModel


class ViewAxis(StrEnum):
    DIRECTION = "DIRECTION"
    ORGANIZATION = "ORGANIZATION"


class ModeViewLeaf(ScopeModel):
    direction_id: str
    organization_id: str
    analysis_unit_ids: tuple[str, ...]
    publication_ids: tuple[str, ...]
    analysis_unit_count: int = Field(ge=0)
    publication_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> "ModeViewLeaf":
        if len(self.analysis_unit_ids) != self.analysis_unit_count:
            raise ValueError("mode view analysis unit count mismatch")
        if len(self.publication_ids) != self.publication_count:
            raise ValueError("mode view publication count mismatch")
        return self


class ModeViewGroup(ScopeModel):
    group_id: str
    group_axis: ViewAxis
    leaves: tuple[ModeViewLeaf, ...]
    analysis_unit_count: int = Field(ge=0)
    publication_count: int = Field(ge=0)


class LandscapeModeView(ScopeModel):
    mode: LandscapeInputMode
    primary_axis: ViewAxis
    secondary_axis: ViewAxis
    groups: tuple[ModeViewGroup, ...]
    comparison_enabled: bool
    confirmed_organization_ids: tuple[str, ...]
    unconfirmed_organization_ids: tuple[str, ...]
    counting_disclosure: str


def build_mode_view(
    cube: MetricCube,
    *,
    mode: LandscapeInputMode,
    confirmed_organization_ids: tuple[str, ...] = (),
) -> LandscapeModeView:
    confirmed = tuple(sorted(set(confirmed_organization_ids)))
    if len(confirmed) != len(confirmed_organization_ids):
        raise ValueError("confirmed organization IDs must be unique and sorted")
    if mode != LandscapeInputMode.TECHNOLOGY_ONLY and not confirmed:
        raise ValueError("company modes require confirmed organizations")
    observed = {cell.organization_id for cell in cube.cells}
    unconfirmed = tuple(sorted(observed - set(confirmed))) if confirmed else ()
    included_cells = tuple(
        cell
        for cell in cube.cells
        if mode == LandscapeInputMode.TECHNOLOGY_ONLY
        or cell.organization_id in set(confirmed)
    )
    leaves = _collapse_cells(included_cells)
    if mode == LandscapeInputMode.COMPANY_ONLY:
        primary_axis = ViewAxis.ORGANIZATION
        secondary_axis = ViewAxis.DIRECTION
        group_ids = confirmed
        group_key = lambda leaf: leaf.organization_id
        leaf_key = lambda leaf: (leaf.direction_id, leaf.organization_id)
    else:
        primary_axis = ViewAxis.DIRECTION
        secondary_axis = ViewAxis.ORGANIZATION
        group_ids = tuple(sorted({leaf.direction_id for leaf in leaves}))
        group_key = lambda leaf: leaf.direction_id
        leaf_key = lambda leaf: (leaf.organization_id, leaf.direction_id)
    by_group: dict[str, list[ModeViewLeaf]] = defaultdict(list)
    for leaf in leaves:
        by_group[group_key(leaf)].append(leaf)
    groups = []
    for group_id in group_ids:
        group_leaves = tuple(sorted(by_group[group_id], key=leaf_key))
        units = {member for leaf in group_leaves for member in leaf.analysis_unit_ids}
        publications = {
            publication for leaf in group_leaves for publication in leaf.publication_ids
        }
        groups.append(
            ModeViewGroup(
                group_id=group_id,
                group_axis=primary_axis,
                leaves=group_leaves,
                analysis_unit_count=len(units),
                publication_count=len(publications),
            )
        )
    comparison_enabled = (
        mode != LandscapeInputMode.TECHNOLOGY_ONLY and len(confirmed) > 1
    )
    return LandscapeModeView(
        mode=mode,
        primary_axis=primary_axis,
        secondary_axis=secondary_axis,
        groups=tuple(groups),
        comparison_enabled=comparison_enabled,
        confirmed_organization_ids=confirmed,
        unconfirmed_organization_ids=unconfirmed,
        counting_disclosure=(
            f"机构口径为 {cube.organization_counting_mode.value}；"
            "Analysis Unit 按族内最早公开日进入一个时间桶，Publication 逐件计数。"
        ),
    )


def _collapse_cells(cells) -> tuple[ModeViewLeaf, ...]:
    units: dict[tuple[str, str], set[str]] = defaultdict(set)
    publications: dict[tuple[str, str], set[str]] = defaultdict(set)
    for cell in cells:
        key = (cell.direction_id, cell.organization_id)
        units[key].update(cell.analysis_unit_ids)
        publications[key].update(cell.publication_ids)
    leaves = []
    for direction_id, organization_id in sorted(set(units) | set(publications)):
        analysis_unit_ids = tuple(sorted(units[(direction_id, organization_id)]))
        publication_ids = tuple(sorted(publications[(direction_id, organization_id)]))
        leaves.append(
            ModeViewLeaf(
                direction_id=direction_id,
                organization_id=organization_id,
                analysis_unit_ids=analysis_unit_ids,
                publication_ids=publication_ids,
                analysis_unit_count=len(analysis_unit_ids),
                publication_count=len(publication_ids),
            )
        )
    return tuple(leaves)


__all__ = [
    "LandscapeModeView",
    "ModeViewGroup",
    "ModeViewLeaf",
    "ViewAxis",
    "build_mode_view",
]
