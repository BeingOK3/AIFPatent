from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Iterable

from pydantic import Field, model_validator

from .scope import ConfirmedScopeRevision, LandscapeInputMode, ScopeModel


class V4SearchQuery(ScopeModel):
    query_id: str = Field(pattern=r"^LQ4-[0-9a-f]{16}$")
    query_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope_revision_id: str = Field(pattern=r"^SCR-[0-9a-f]{16}$")
    mode: LandscapeInputMode
    company_profile_id: str | None = None
    company_name_id: str | None = None
    company_name: str | None = Field(default=None, max_length=300)
    term_ids: tuple[str, ...] = Field(default=(), max_length=8)
    terms: tuple[str, ...] = Field(default=(), max_length=8)
    publication_start: date
    publication_end: date
    query_text: str = Field(min_length=1, max_length=1200)

    @model_validator(mode="after")
    def validate_query_shape(self) -> "V4SearchQuery":
        company_fields = (
            self.company_profile_id,
            self.company_name_id,
            self.company_name,
        )
        if any(company_fields) != all(company_fields):
            raise ValueError("company query fields must be all present or all absent")
        if len(self.term_ids) != len(self.terms):
            raise ValueError("technology term IDs and values must align")
        has_company = bool(self.company_name)
        has_technology = bool(self.terms)
        expected = (
            LandscapeInputMode.COMPANY_AND_TECHNOLOGY
            if has_company and has_technology
            else LandscapeInputMode.COMPANY_ONLY
            if has_company
            else LandscapeInputMode.TECHNOLOGY_ONLY
            if has_technology
            else None
        )
        if expected is None or self.mode != expected:
            raise ValueError("query shape does not match its input mode")
        if self.publication_end < self.publication_start:
            raise ValueError("query publication date range is reversed")
        return self


class V4QueryPlan(ScopeModel):
    scope_revision_id: str = Field(pattern=r"^SCR-[0-9a-f]{16}$")
    scope_revision_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    queries: tuple[V4SearchQuery, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_queries(self) -> "V4QueryPlan":
        identifiers = [query.query_id for query in self.queries]
        hashes = [query.query_hash for query in self.queries]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("query plan contains duplicate query IDs")
        if len(hashes) != len(set(hashes)):
            raise ValueError("query plan contains duplicate query semantics")
        if any(query.scope_revision_id != self.scope_revision_id for query in self.queries):
            raise ValueError("query belongs to a different scope revision")
        return self


def build_query_plan(
    scope: ConfirmedScopeRevision,
    *,
    max_terms_per_group: int = 8,
    max_group_characters: int = 600,
) -> V4QueryPlan:
    if not 1 <= max_terms_per_group <= 8:
        raise ValueError("max_terms_per_group must be between 1 and 8")
    if not 100 <= max_group_characters <= 900:
        raise ValueError("max_group_characters must be between 100 and 900")

    company_names = [
        (company.profile_id, name.name_id, name.text)
        for company in scope.companies
        for name in company.names
    ]
    technology_groups = _group_terms(
        ((term.term_id, term.text) for term in scope.technology_terms),
        max_terms=max_terms_per_group,
        max_characters=max_group_characters,
    )
    shapes: list[tuple[tuple[str, str, str] | None, tuple[tuple[str, str], ...]]] = []
    if scope.mode == LandscapeInputMode.COMPANY_ONLY:
        shapes.extend((company, ()) for company in company_names)
    elif scope.mode == LandscapeInputMode.TECHNOLOGY_ONLY:
        shapes.extend((None, group) for group in technology_groups)
    else:
        shapes.extend(
            (company, group)
            for company in company_names
            for group in technology_groups
        )

    queries = tuple(_make_query(scope, company, group) for company, group in shapes)
    semantic = {
        "scope_revision_id": scope.scope_revision_id,
        "scope_revision_hash": scope.scope_revision_hash,
        "queries": [query.model_dump(mode="json") for query in queries],
    }
    return V4QueryPlan(
        scope_revision_id=scope.scope_revision_id,
        scope_revision_hash=scope.scope_revision_hash,
        plan_hash=_hash_json(semantic),
        queries=queries,
    )


def _group_terms(
    values: Iterable[tuple[str, str]],
    *,
    max_terms: int,
    max_characters: int,
) -> tuple[tuple[tuple[str, str], ...], ...]:
    groups: list[tuple[tuple[str, str], ...]] = []
    current: list[tuple[str, str]] = []
    current_characters = 0
    for identity, text in values:
        addition = len(text) + (4 if current else 0)
        if current and (
            len(current) == max_terms
            or current_characters + addition > max_characters
        ):
            groups.append(tuple(current))
            current = []
            current_characters = 0
            addition = len(text)
        current.append((identity, text))
        current_characters += addition
    if current:
        groups.append(tuple(current))
    return tuple(groups)


def _make_query(
    scope: ConfirmedScopeRevision,
    company: tuple[str, str, str] | None,
    terms: tuple[tuple[str, str], ...],
) -> V4SearchQuery:
    company_profile_id, company_name_id, company_name = company or (None, None, None)
    term_ids = tuple(identity for identity, _text in terms)
    term_values = tuple(text for _identity, text in terms)
    semantic = {
        "scope_revision_id": scope.scope_revision_id,
        "mode": scope.mode.value,
        "company_profile_id": company_profile_id,
        "company_name_id": company_name_id,
        "company_name": company_name,
        "term_ids": term_ids,
        "terms": term_values,
        "publication_start": scope.publication_start.isoformat(),
        "publication_end": scope.publication_end.isoformat(),
    }
    query_hash = _hash_json(semantic)
    fragments = []
    if company_name:
        fragments.append(f'assignee:"{_escape_phrase(company_name)}"')
    if term_values:
        fragments.append(
            "(" + " OR ".join(f'"{_escape_phrase(term)}"' for term in term_values) + ")"
        )
    return V4SearchQuery(
        query_id=f"LQ4-{query_hash[:16]}",
        query_hash=query_hash,
        scope_revision_id=scope.scope_revision_id,
        mode=scope.mode,
        company_profile_id=company_profile_id,
        company_name_id=company_name_id,
        company_name=company_name,
        term_ids=term_ids,
        terms=term_values,
        publication_start=scope.publication_start,
        publication_end=scope.publication_end,
        query_text=" AND ".join(fragments),
    )


def _escape_phrase(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _hash_json(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = ["V4QueryPlan", "V4SearchQuery", "build_query_plan"]
