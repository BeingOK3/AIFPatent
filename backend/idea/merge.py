from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field

from .providers import SearchHit


class MergeModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceRecord(MergeModel):
    provider: str
    provider_rank: int
    query_id: str
    url: str
    raw: dict[str, Any]


class MergedHit(MergeModel):
    merge_key: str
    publication_number: str | None = None
    application_number: str | None = None
    family_id: str | None = None
    title: str = ""
    urls: list[str] = []
    snippet: str = ""
    priority_date: str | None = None
    filing_date: str | None = None
    publication_date: str | None = None
    assignee: str | None = None
    found_by: list[str]
    query_ids: list[str]
    sources: list[SourceRecord]
    possible_family_keys: list[str] = []


def normalize_identifier(value: str | None) -> str | None:
    if not value:
        return None
    normalized = "".join(character for character in value.upper() if character.isalnum())
    return normalized or None


def normalize_publication_number(value: str | None) -> str | None:
    return normalize_identifier(value)


def normalize_application_number(value: str | None) -> str | None:
    return normalize_identifier(value)


def normalize_family_id(value: str | None) -> str | None:
    return normalize_identifier(value)


def _text_key(value: str | None) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKC", value).lower()
    return "".join(character for character in normalized if character.isalnum())


def _possible_family_key(hit: SearchHit) -> str | None:
    title = _text_key(hit.title)
    assignee = _text_key(hit.assignee)
    if len(title) < 8 or not hit.priority_date:
        return None
    return f"possible:{title}:{hit.priority_date}:{assignee}"


def _identity_keys(hit: SearchHit) -> list[str]:
    keys = []
    publication = normalize_publication_number(hit.publication_number)
    application = normalize_application_number(hit.application_number)
    family = normalize_family_id(hit.family_id)
    if publication:
        keys.append(f"publication:{publication}")
    if application:
        keys.append(f"application:{application}")
    if family:
        keys.append(f"family:{family}")
    if not keys:
        keys.append(f"url:{hit.url}")
    return keys


class _DisjointSet:
    def __init__(self, size: int):
        self.parent = list(range(size))

    def find(self, item: int) -> int:
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def merge_hits(batches: Iterable[tuple[str, list[SearchHit]]]) -> list[MergedHit]:
    records: list[tuple[str, SearchHit]] = []
    for query_id, hits in batches:
        records.extend((query_id, hit) for hit in hits)
    if not records:
        return []

    sets = _DisjointSet(len(records))
    key_owner: dict[str, int] = {}
    for index, (_, hit) in enumerate(records):
        for key in _identity_keys(hit):
            owner = key_owner.get(key)
            if owner is None:
                key_owner[key] = index
            else:
                sets.union(owner, index)

    groups: dict[int, list[tuple[str, SearchHit]]] = defaultdict(list)
    for index, record in enumerate(records):
        groups[sets.find(index)].append(record)

    possible_groups: dict[str, list[int]] = defaultdict(list)
    for root, items in groups.items():
        for _, hit in items:
            possible = _possible_family_key(hit)
            if possible:
                possible_groups[possible].append(root)

    merged = []
    for root, items in groups.items():
        publications = _unique(
            normalize_publication_number(hit.publication_number) for _, hit in items
        )
        applications = _unique(
            normalize_application_number(hit.application_number) for _, hit in items
        )
        families = _unique(normalize_family_id(hit.family_id) for _, hit in items)
        identity = (
            f"publication:{publications[0]}"
            if publications
            else f"application:{applications[0]}"
            if applications
            else f"family:{families[0]}"
            if families
            else f"url:{items[0][1].url}"
        )
        possible = []
        for key, roots in possible_groups.items():
            if root in roots and len(set(roots)) > 1:
                possible.append(key)
        sources = [
            SourceRecord(
                provider=hit.provider,
                provider_rank=hit.provider_rank,
                query_id=query_id,
                url=hit.url,
                raw=hit.raw,
            )
            for query_id, hit in items
        ]
        merged.append(
            MergedHit(
                merge_key=identity,
                publication_number=publications[0] if publications else None,
                application_number=applications[0] if applications else None,
                family_id=families[0] if families else None,
                title=_richest(hit.title for _, hit in items),
                urls=_unique(hit.url for _, hit in items),
                snippet=_richest(hit.snippet for _, hit in items),
                priority_date=_first(hit.priority_date for _, hit in items),
                filing_date=_first(hit.filing_date for _, hit in items),
                publication_date=_first(hit.publication_date for _, hit in items),
                assignee=_richest(hit.assignee for _, hit in items) or None,
                found_by=_unique(hit.provider for _, hit in items),
                query_ids=_unique(query_id for query_id, _ in items),
                sources=sources,
                possible_family_keys=sorted(possible),
            )
        )
    return sorted(
        merged,
        key=lambda hit: (
            min(source.provider_rank for source in hit.sources),
            hit.publication_number or hit.title,
        ),
    )


def _unique(values) -> list[str]:
    result = []
    seen = set()
    for value in values:
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _richest(values) -> str:
    candidates = [value.strip() for value in values if value and value.strip()]
    return max(candidates, key=len, default="")


def _first(values) -> str | None:
    return next((value for value in values if value), None)
