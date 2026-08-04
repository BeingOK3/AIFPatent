from __future__ import annotations

import hashlib
import re
import unicodedata
from enum import StrEnum
from typing import Mapping, Sequence

from pydantic import Field, model_validator

from .scope import ConfirmedScopeRevision, LandscapeInputMode, ScopeModel


ORGANIZATION_POLICY_VERSION = "landscape-organization/1"
UNKNOWN_ORGANIZATION_ID = "ORG-UNKNOWN"


class OrganizationAssignmentError(ValueError):
    pass


class OrganizationType(StrEnum):
    COMPANY = "COMPANY"
    ACADEMIC_RESEARCH = "ACADEMIC_RESEARCH"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class Organization(ScopeModel):
    organization_id: str = Field(pattern=r"^ORG-(?:[0-9a-f]{16}|UNKNOWN)$")
    display_name: str = Field(min_length=1, max_length=1000)
    normalized_name: str = Field(min_length=1, max_length=1000)
    organization_type: OrganizationType
    source_profile_id: str | None = Field(default=None, max_length=100)
    observed_names: tuple[str, ...] = Field(default=(), max_length=100)

    @model_validator(mode="after")
    def validate_identity(self) -> "Organization":
        if self.organization_id == UNKNOWN_ORGANIZATION_ID:
            if self.organization_type != OrganizationType.UNKNOWN:
                raise ValueError("unknown organization must have UNKNOWN type")
            return self
        identity = self.source_profile_id or self.normalized_name
        expected = f"ORG-{hashlib.sha256(identity.encode()).hexdigest()[:16]}"
        if self.organization_id != expected:
            raise ValueError("organization ID does not match identity")
        if not self.observed_names:
            raise ValueError("known organization must retain observed names")
        return self


class PublicationOrganizationAssignment(ScopeModel):
    publication_id: str = Field(pattern=r"^PUB-[0-9a-f]{16}$")
    primary_organization_id: str = Field(pattern=r"^ORG-(?:[0-9a-f]{16}|UNKNOWN)$")
    co_organization_ids: tuple[str, ...] = Field(default=(), max_length=100)
    observed_applicants: tuple[str, ...] = Field(default=(), max_length=100)
    unconfirmed_applicants: tuple[str, ...] = Field(default=(), max_length=100)

    @model_validator(mode="after")
    def validate_partition(self) -> "PublicationOrganizationAssignment":
        if len(self.co_organization_ids) != len(set(self.co_organization_ids)):
            raise ValueError("co-organization IDs must be unique")
        if self.primary_organization_id in self.co_organization_ids:
            raise ValueError("primary organization cannot also be a co-organization")
        if len(self.observed_applicants) != len(set(self.observed_applicants)):
            raise ValueError("observed applicants must be unique")
        if any(item not in self.observed_applicants for item in self.unconfirmed_applicants):
            raise ValueError("unconfirmed applicant must be an observed applicant")
        return self


class OrganizationAssignmentSet(ScopeModel):
    run_id: str = Field(min_length=1, max_length=100)
    policy_version: str = ORGANIZATION_POLICY_VERSION
    organizations: tuple[Organization, ...]
    assignments: tuple[PublicationOrganizationAssignment, ...]

    @model_validator(mode="after")
    def validate_references(self) -> "OrganizationAssignmentSet":
        organization_ids = [item.organization_id for item in self.organizations]
        if len(organization_ids) != len(set(organization_ids)):
            raise ValueError("organization IDs must be unique")
        publication_ids = [item.publication_id for item in self.assignments]
        if len(publication_ids) != len(set(publication_ids)):
            raise ValueError("publication assignments must be unique")
        known = set(organization_ids)
        referenced = {
            organization_id
            for item in self.assignments
            for organization_id in (
                item.primary_organization_id,
                *item.co_organization_ids,
            )
        }
        if not referenced <= known:
            raise ValueError("assignment references an unknown organization ID")
        return self


def assign_organizations(
    run_id: str,
    scope: ConfirmedScopeRevision,
    applicants_by_publication: Mapping[str, Sequence[str]],
) -> OrganizationAssignmentSet:
    """Create deterministic organization assignments without corporate inference.

    Company modes use only exact normalized names confirmed in the frozen scope.
    Technology-only mode groups only identical normalized provider observations.
    Input order defines the primary applicant; all remaining known applicants are
    retained for the all-known counting view.
    """

    normalized_observations = {
        publication_id: _unique_names(names)
        for publication_id, names in applicants_by_publication.items()
    }
    if scope.mode == LandscapeInputMode.TECHNOLOGY_ONLY:
        organizations, owner_by_name = _observed_organizations(normalized_observations)
    else:
        organizations, owner_by_name = _confirmed_organizations(scope)

    assignments: list[PublicationOrganizationAssignment] = []
    needs_unknown = False
    for publication_id in sorted(normalized_observations):
        observed = normalized_observations[publication_id]
        resolved = [owner_by_name.get(normalize_organization_name(name)) for name in observed]
        unconfirmed = tuple(name for name, owner in zip(observed, resolved) if owner is None)
        primary = resolved[0] if resolved else None
        if primary is None:
            primary = UNKNOWN_ORGANIZATION_ID
            needs_unknown = True
        co_ids = tuple(
            dict.fromkeys(
                owner
                for owner in resolved[1:]
                if owner is not None and owner != primary
            )
        )
        assignments.append(
            PublicationOrganizationAssignment(
                publication_id=publication_id,
                primary_organization_id=primary,
                co_organization_ids=co_ids,
                observed_applicants=observed,
                unconfirmed_applicants=unconfirmed,
            )
        )
    if needs_unknown:
        organizations.append(
            Organization(
                organization_id=UNKNOWN_ORGANIZATION_ID,
                display_name="Unknown / unconfirmed",
                normalized_name="unknown",
                organization_type=OrganizationType.UNKNOWN,
            )
        )
    return OrganizationAssignmentSet(
        run_id=run_id,
        organizations=tuple(sorted(organizations, key=lambda item: item.organization_id)),
        assignments=tuple(assignments),
    )


def normalize_organization_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE)
    return " ".join(normalized.split())


def _confirmed_organizations(
    scope: ConfirmedScopeRevision,
) -> tuple[list[Organization], dict[str, str]]:
    organizations: list[Organization] = []
    owner_by_name: dict[str, str] = {}
    for company in scope.companies:
        identity = company.profile_id
        organization_id = f"ORG-{hashlib.sha256(identity.encode()).hexdigest()[:16]}"
        observed_names = tuple(item.text for item in company.names)
        organizations.append(
            Organization(
                organization_id=organization_id,
                display_name=company.display_name,
                normalized_name=normalize_organization_name(company.display_name),
                organization_type=OrganizationType.COMPANY,
                source_profile_id=company.profile_id,
                observed_names=observed_names,
            )
        )
        for name in observed_names:
            key = normalize_organization_name(name)
            previous = owner_by_name.setdefault(key, organization_id)
            if previous != organization_id:
                raise OrganizationAssignmentError(
                    f"confirmed organization name belongs to multiple companies: {name}"
                )
    return organizations, owner_by_name


def _observed_organizations(
    observations: Mapping[str, tuple[str, ...]],
) -> tuple[list[Organization], dict[str, str]]:
    variants: dict[str, set[str]] = {}
    for names in observations.values():
        for name in names:
            variants.setdefault(normalize_organization_name(name), set()).add(name)
    organizations: list[Organization] = []
    owner_by_name: dict[str, str] = {}
    for key in sorted(variants):
        if not key:
            continue
        names = tuple(sorted(variants[key], key=lambda item: (item.casefold(), item)))
        organization_id = f"ORG-{hashlib.sha256(key.encode()).hexdigest()[:16]}"
        owner_by_name[key] = organization_id
        organizations.append(
            Organization(
                organization_id=organization_id,
                display_name=names[0],
                normalized_name=key,
                organization_type=_infer_type(key),
                observed_names=names,
            )
        )
    return organizations, owner_by_name


def _unique_names(names: Sequence[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in names:
        cleaned = " ".join(value.split())
        key = normalize_organization_name(cleaned)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return tuple(result)


_ACADEMIC_MARKERS = (
    "university", "college", "academy", "school", "研究院", "研究所",
    "大学", "学院", "高校", "科学院",
)
_COMPANY_MARKERS = (
    " ltd", " limited", " inc", " corporation", " corp", " company",
    " llc", " gmbh", "株式会社", "有限公司", "股份有限公司", "公司",
)


def _infer_type(normalized_name: str) -> OrganizationType:
    padded = f" {normalized_name}"
    if any(marker in normalized_name for marker in _ACADEMIC_MARKERS):
        return OrganizationType.ACADEMIC_RESEARCH
    if any(marker in padded for marker in _COMPANY_MARKERS):
        return OrganizationType.COMPANY
    return OrganizationType.OTHER


__all__ = [
    "ORGANIZATION_POLICY_VERSION",
    "UNKNOWN_ORGANIZATION_ID",
    "Organization",
    "OrganizationAssignmentError",
    "OrganizationAssignmentSet",
    "OrganizationType",
    "PublicationOrganizationAssignment",
    "assign_organizations",
    "normalize_organization_name",
]
