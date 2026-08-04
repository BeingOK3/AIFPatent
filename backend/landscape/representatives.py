from __future__ import annotations

import hashlib
import re
from datetime import date
from enum import StrEnum
from urllib.parse import urlparse

from pydantic import Field, model_validator

from .metrics import MetricAnalysisUnit, MetricPublication
from .scope import ScopeModel


_PUBLICATION_NUMBER = re.compile(r"^[A-Z0-9][A-Z0-9.-]{2,99}$")
_ALLOWED_HOSTS = {"patents.google.com"}


class PatentLinkStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"


class RepresentativePatent(ScopeModel):
    representative_id: str = Field(pattern=r"^REP-[0-9a-f]{16}$")
    selection_rank: int = Field(ge=1, le=10)
    direction_id: str
    analysis_unit_id: str
    publication_id: str
    title: str
    publication_number: str
    organization_ids: tuple[str, ...]
    publication_date: date
    classification_path: tuple[str, ...]
    selection_reasons: tuple[str, ...] = Field(min_length=1)
    patent_url: str | None = None
    link_status: PatentLinkStatus
    link_target: str = "_blank"
    link_rel: str = "noopener noreferrer"

    @model_validator(mode="after")
    def validate_link(self) -> "RepresentativePatent":
        if self.link_status == PatentLinkStatus.AVAILABLE:
            if not self.patent_url or urlparse(self.patent_url).hostname not in _ALLOWED_HOSTS:
                raise ValueError("representative patent URL is outside allowlist")
            if urlparse(self.patent_url).scheme != "https":
                raise ValueError("representative patent URL must use HTTPS")
        elif self.patent_url is not None:
            raise ValueError("unavailable patent link must not contain URL")
        if self.link_target != "_blank" or self.link_rel != "noopener noreferrer":
            raise ValueError("representative link safety attributes are fixed")
        return self


class RepresentativeExplanationProposal(ScopeModel):
    representative_id: str = Field(pattern=r"^REP-[0-9a-f]{16}$")
    analysis_unit_id: str
    publication_id: str
    explanation: str = Field(min_length=1, max_length=1500)


class RepresentativeExplanation(ScopeModel):
    representative_id: str
    explanation: str
    source: str = Field(pattern=r"^(MODEL|DETERMINISTIC)$")


def select_representative_patents(
    units: tuple[MetricAnalysisUnit, ...],
    *,
    direction_id: str,
    eligible_analysis_unit_ids: tuple[str, ...] | None = None,
    limit: int = 3,
) -> tuple[RepresentativePatent, ...]:
    if limit < 1 or limit > 10:
        raise ValueError("representative limit must be between 1 and 10")
    unit_by_id = {unit.analysis_unit_id: unit for unit in units}
    if len(unit_by_id) != len(units):
        raise ValueError("duplicate representative analysis unit")
    eligible = set(eligible_analysis_unit_ids) if eligible_analysis_unit_ids is not None else None
    if eligible is not None:
        unknown = sorted(eligible - set(unit_by_id))
        if unknown:
            raise ValueError(f"representative eligibility cites unknown units: {unknown}")
    candidates = [
        unit
        for unit in units
        if unit.direction_id == direction_id
        and (eligible is None or unit.analysis_unit_id in eligible)
    ]
    selected: list[MetricAnalysisUnit] = []
    covered_organizations: set[str] = set()
    covered_years: set[int] = set()
    while candidates and len(selected) < limit:
        best = min(
            candidates,
            key=lambda unit: _rank_key(unit, covered_organizations, covered_years),
        )
        selected.append(best)
        candidates.remove(best)
        publication = _representative_publication(best)
        covered_organizations.update(_organization_ids(publication))
        covered_years.add(publication.publication_date.year)
    return tuple(
        _to_representative(unit, selection_rank=index)
        for index, unit in enumerate(selected, start=1)
    )


def apply_representative_explanation(
    representative: RepresentativePatent,
    proposal: RepresentativeExplanationProposal,
) -> RepresentativeExplanation:
    if proposal.representative_id != representative.representative_id:
        raise ValueError("explanation belongs to another representative")
    if (
        proposal.analysis_unit_id != representative.analysis_unit_id
        or proposal.publication_id != representative.publication_id
    ):
        raise ValueError("model cannot change representative patent membership")
    return RepresentativeExplanation(
        representative_id=representative.representative_id,
        explanation=proposal.explanation,
        source="MODEL",
    )


def deterministic_representative_explanation(
    representative: RepresentativePatent,
) -> RepresentativeExplanation:
    return RepresentativeExplanation(
        representative_id=representative.representative_id,
        explanation="；".join(representative.selection_reasons),
        source="DETERMINISTIC",
    )


def google_patents_url(publication_number: str) -> str | None:
    normalized = "".join(publication_number.upper().split())
    if not _PUBLICATION_NUMBER.fullmatch(normalized):
        return None
    url = f"https://patents.google.com/patent/{normalized}"
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_HOSTS:
        return None
    return url


def _rank_key(
    unit: MetricAnalysisUnit,
    covered_organizations: set[str],
    covered_years: set[int],
) -> tuple[float, str]:
    publication = _representative_publication(unit)
    organizations = set(_organization_ids(publication))
    diversity_bonus = 0.12 if organizations - covered_organizations else 0
    time_bonus = 0.08 if publication.publication_date.year not in covered_years else 0
    score = (
        unit.classification_confidence * 0.35
        + unit.evidence_completeness * 0.3
        + unit.direction_centrality * 0.23
        + diversity_bonus
        + time_bonus
    )
    return -score, unit.analysis_unit_id


def _representative_publication(unit: MetricAnalysisUnit) -> MetricPublication:
    return min(
        unit.publications,
        key=lambda publication: (publication.publication_date, publication.publication_id),
    )


def _organization_ids(publication: MetricPublication) -> tuple[str, ...]:
    return tuple(
        sorted({publication.primary_organization_id, *publication.co_organization_ids})
    )


def _to_representative(
    unit: MetricAnalysisUnit,
    *,
    selection_rank: int,
) -> RepresentativePatent:
    publication = _representative_publication(unit)
    url = google_patents_url(publication.publication_number)
    identity = f"{unit.direction_id}|{unit.analysis_unit_id}|{publication.publication_id}"
    reasons = [
        f"分类置信度 {unit.classification_confidence:.2f}",
        f"摘要证据完整度 {unit.evidence_completeness:.2f}",
        f"方向中心接近度 {unit.direction_centrality:.2f}",
        "同一专利族仅选一个公开号",
    ]
    return RepresentativePatent(
        representative_id=f"REP-{hashlib.sha256(identity.encode()).hexdigest()[:16]}",
        selection_rank=selection_rank,
        direction_id=unit.direction_id,
        analysis_unit_id=unit.analysis_unit_id,
        publication_id=publication.publication_id,
        title=publication.title or publication.publication_number,
        publication_number=publication.publication_number,
        organization_ids=_organization_ids(publication),
        publication_date=publication.publication_date,
        classification_path=unit.classification_path,
        selection_reasons=tuple(reasons),
        patent_url=url,
        link_status=(PatentLinkStatus.AVAILABLE if url else PatentLinkStatus.UNAVAILABLE),
    )


__all__ = [
    "PatentLinkStatus",
    "RepresentativeExplanation",
    "RepresentativeExplanationProposal",
    "RepresentativePatent",
    "apply_representative_explanation",
    "deterministic_representative_explanation",
    "google_patents_url",
    "select_representative_patents",
]
