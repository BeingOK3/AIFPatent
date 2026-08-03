from __future__ import annotations

import hashlib
import json
from datetime import date

from pydantic import Field, model_validator

from idea.providers.base import SearchHit

from .scope import ScopeModel


class PublicationFreezeError(ValueError):
    pass


class FrozenPublication(ScopeModel):
    publication_id: str = Field(pattern=r"^PUB-[0-9a-f]{16}$")
    publication_identity: str = Field(min_length=1, max_length=300)
    publication_number: str | None = None
    title: str = Field(default="", max_length=2000)
    url: str = Field(min_length=1, max_length=4000)
    publication_date: date | None = None
    family_id: str | None = None
    source_queries: tuple[str, ...] = Field(min_length=1)
    provider: str = Field(min_length=1, max_length=100)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_identity(self) -> "FrozenPublication":
        if self.publication_number is None and self.publication_identity.startswith("url:") is False:
            raise ValueError("missing publication identity")
        if self.publication_id != f"PUB-{hashlib.sha256(self.publication_identity.encode()).hexdigest()[:16]}":
            raise ValueError("publication ID does not match identity")
        return self


class FrozenPublicationSet(ScopeModel):
    run_id: str = Field(min_length=1)
    publications: tuple[FrozenPublication, ...]
    publication_count: int = Field(ge=0)
    analysis_unit_count: int = Field(ge=0)
    freeze_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_set(self) -> "FrozenPublicationSet":
        if self.publication_count != len(self.publications):
            raise ValueError("publication count mismatch")
        if self.analysis_unit_count != len(self.publications):
            raise ValueError("analysis unit count mismatch")
        if len({item.publication_identity for item in self.publications}) != len(self.publications):
            raise ValueError("duplicate frozen publication identity")
        semantic = [item.model_dump(mode="json") for item in self.publications]
        expected = hashlib.sha256(
            json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if self.freeze_hash != expected:
            raise ValueError("frozen publication hash mismatch")
        return self


def freeze_publications(
    run_id: str,
    hits: tuple[tuple[str, SearchHit], ...],
) -> FrozenPublicationSet:
    by_identity: dict[str, dict] = {}
    for query_id, hit in hits:
        identity = _identity(hit)
        current = by_identity.get(identity)
        source_queries = set(current["source_queries"]) if current else set()
        source_queries.add(query_id)
        if current is None or _rank_key(hit) < current["rank_key"]:
            by_identity[identity] = {
                "hit": hit,
                "source_queries": tuple(sorted(source_queries)),
                "rank_key": _rank_key(hit),
            }
        else:
            current["source_queries"] = tuple(sorted(source_queries))
    publications = []
    for identity in sorted(by_identity):
        item = by_identity[identity]
        hit = item["hit"]
        publications.append(_freeze_one(identity, item["source_queries"], hit))
    frozen = tuple(publications)
    freeze_hash = hashlib.sha256(
        json.dumps([item.model_dump(mode="json") for item in frozen], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return FrozenPublicationSet(
        run_id=run_id,
        publications=frozen,
        publication_count=len(frozen),
        analysis_unit_count=len(frozen),
        freeze_hash=freeze_hash,
    )


def _identity(hit: SearchHit) -> str:
    if hit.publication_number and hit.publication_number.strip():
        return "publication:" + _normalize(hit.publication_number)
    if hit.url and hit.url.strip():
        return "url:" + hit.url.strip()
    raise PublicationFreezeError("search hit has no publication identity")


def _normalize(value: str) -> str:
    return "".join(value.upper().split())


def _rank_key(hit: SearchHit) -> tuple[int, str, str]:
    return (hit.provider_rank, hit.provider, hit.url)


def _freeze_one(identity: str, queries: tuple[str, ...], hit: SearchHit) -> FrozenPublication:
    content = {"identity": identity, "title": hit.title, "url": hit.url, "family_id": hit.family_id}
    return FrozenPublication(
        publication_id=f"PUB-{hashlib.sha256(identity.encode()).hexdigest()[:16]}",
        publication_identity=identity,
        publication_number=_normalize(hit.publication_number) if hit.publication_number else None,
        title=hit.title,
        url=hit.url or f"https://patents.google.com/patent/{_normalize(hit.publication_number)}",
        publication_date=_parse_date(hit.publication_date),
        family_id=hit.family_id,
        source_queries=queries,
        provider=hit.provider,
        content_hash=hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
    )


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


__all__ = ["FrozenPublication", "FrozenPublicationSet", "PublicationFreezeError", "freeze_publications"]
