from __future__ import annotations

import hashlib
import json

from pydantic import Field, model_validator

from .scope import ScopeModel


class FamilyResolutionError(ValueError):
    pass


class BibliographicPublication(ScopeModel):
    publication_id: str = Field(min_length=1, max_length=100)
    publication_number: str = Field(min_length=1, max_length=100)
    application_number: str | None = Field(default=None, max_length=100)
    priority_numbers: tuple[str, ...] = Field(default=(), max_length=100)
    relationship: str | None = Field(default=None, max_length=100)
    related_application: str | None = Field(default=None, max_length=100)
    family_id: str | None = Field(default=None, max_length=200)


class AnalysisUnit(ScopeModel):
    analysis_unit_id: str = Field(pattern=r"^AU-[0-9a-f]{16}$")
    member_publication_ids: tuple[str, ...] = Field(min_length=1)
    merge_basis: str = Field(min_length=1, max_length=100)


class FamilyResolution(ScopeModel):
    analysis_units: tuple[AnalysisUnit, ...]
    publication_count: int = Field(ge=0)
    analysis_unit_count: int = Field(ge=0)
    resolution_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_partition(self) -> "FamilyResolution":
        members = [member for unit in self.analysis_units for member in unit.member_publication_ids]
        if len(members) != self.publication_count or len(members) != len(set(members)):
            raise ValueError("analysis units are not a strict publication partition")
        if len(self.analysis_units) != self.analysis_unit_count:
            raise ValueError("analysis unit count mismatch")
        expected = _hash([unit.model_dump(mode="json") for unit in self.analysis_units])
        if expected != self.resolution_hash:
            raise ValueError("family resolution hash mismatch")
        return self


def resolve_families(
    publications: tuple[BibliographicPublication, ...],
) -> FamilyResolution:
    identifiers = [item.publication_id for item in publications]
    if len(identifiers) != len(set(identifiers)):
        raise FamilyResolutionError("duplicate publication ID")
    parent = {identity: identity for identity in identifiers}

    def find(identity: str) -> str:
        while parent[identity] != identity:
            parent[identity] = parent[parent[identity]]
            identity = parent[identity]
        return identity

    def union(values: list[str]) -> None:
        root = min(find(value) for value in values)
        for value in values:
            parent[find(value)] = root

    by_application: dict[str, list[str]] = {}
    for item in publications:
        application = _normalize(item.application_number)
        if application:
            by_application.setdefault(application, []).append(item.publication_id)
    for members in by_application.values():
        if len(members) > 1:
            union(members)

    by_priorities: dict[tuple[str, ...], list[str]] = {}
    for item in publications:
        priorities = tuple(sorted({_normalize(value) for value in item.priority_numbers if _normalize(value)}))
        # Divisional, continuation and parent/child records remain separate even
        # if they share priority data. Provider family_id is intentionally ignored.
        if priorities and not item.relationship and not item.related_application:
            by_priorities.setdefault(priorities, []).append(item.publication_id)
    for members in by_priorities.values():
        if len(members) > 1:
            union(members)

    grouped: dict[str, list[str]] = {}
    for identity in identifiers:
        grouped.setdefault(find(identity), []).append(identity)
    units = []
    lookup = {item.publication_id: item for item in publications}
    for members in sorted((tuple(sorted(values)) for values in grouped.values())):
        applications = {_normalize(lookup[value].application_number) for value in members}
        applications.discard("")
        basis = (
            "SAME_APPLICATION"
            if len(members) > 1 and len(applications) == 1
            else "EXACT_SIMPLE_PRIORITY_SET"
            if len(members) > 1
            else "CONSERVATIVE_SINGLETON"
        )
        digest = hashlib.sha256("|".join(members).encode()).hexdigest()
        units.append(
            AnalysisUnit(
                analysis_unit_id=f"AU-{digest[:16]}",
                member_publication_ids=members,
                merge_basis=basis,
            )
        )
    frozen = tuple(units)
    return FamilyResolution(
        analysis_units=frozen,
        publication_count=len(publications),
        analysis_unit_count=len(frozen),
        resolution_hash=_hash([unit.model_dump(mode="json") for unit in frozen]),
    )


def _normalize(value: str | None) -> str:
    return "" if not value else "".join(value.upper().split())


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


__all__ = ["AnalysisUnit", "BibliographicPublication", "FamilyResolution", "FamilyResolutionError", "resolve_families"]
