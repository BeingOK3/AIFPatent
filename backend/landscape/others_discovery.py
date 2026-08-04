from __future__ import annotations

import hashlib
import heapq
import math
import re
from collections import Counter
from enum import StrEnum

from pydantic import Field, model_validator

from .classification_terminal import ClassificationResult, ClassificationTerminal
from .direction_record import DirectionRecord, DirectionStatus
from .scope import ScopeModel


ALGORITHM_VERSION = "char-ngram-complete-link/1"


class OthersClusterKind(StrEnum):
    CANDIDATE = "CANDIDATE"
    SINGLETON = "SINGLETON"
    NOISE = "NOISE"


class OthersCluster(ScopeModel):
    cluster_id: str = Field(pattern=r"^OC-[0-9a-f]{16}$")
    algorithm_version: str = ALGORITHM_VERSION
    kind: OthersClusterKind
    member_ids: tuple[str, ...] = Field(min_length=1)
    representative_analysis_unit_id: str
    cohesion: float = Field(ge=0, le=1)
    name: str = Field(min_length=1, max_length=300)
    technical_problem: str = Field(default="", max_length=1000)
    common_mechanism: str = Field(default="", max_length=1500)
    direction_boundary: str = Field(default="", max_length=1000)
    keywords: tuple[str, ...] = Field(default=(), max_length=20)
    naming_source: str = Field(default="DETERMINISTIC", pattern=r"^(DETERMINISTIC|MODEL)$")

    @model_validator(mode="after")
    def validate_members(self) -> "OthersCluster":
        if tuple(sorted(set(self.member_ids))) != self.member_ids:
            raise ValueError("Others cluster members must be unique and sorted")
        if self.representative_analysis_unit_id not in self.member_ids:
            raise ValueError("representative must belong to Others cluster")
        if self.kind != OthersClusterKind.CANDIDATE and len(self.member_ids) != 1:
            raise ValueError("singleton and noise clusters must contain exactly one member")
        return self


class OthersNamingProposal(ScopeModel):
    cluster_id: str = Field(pattern=r"^OC-[0-9a-f]{16}$")
    member_ids: tuple[str, ...] = Field(min_length=1)
    name: str = Field(min_length=1, max_length=300)
    technical_problem: str = Field(default="", max_length=1000)
    common_mechanism: str = Field(default="", max_length=1500)
    direction_boundary: str = Field(default="", max_length=1000)
    keywords: tuple[str, ...] = Field(default=(), max_length=20)


class OthersDiscovery(ScopeModel):
    algorithm_version: str = ALGORITHM_VERSION
    source_member_ids: tuple[str, ...]
    clusters: tuple[OthersCluster, ...]

    @model_validator(mode="after")
    def validate_partition(self) -> "OthersDiscovery":
        members = tuple(
            sorted(member for cluster in self.clusters for member in cluster.member_ids)
        )
        if members != self.source_member_ids:
            raise ValueError("Others clusters must exactly partition source members")
        return self


def discover_others_directions(
    classifications: tuple[ClassificationResult, ...],
    directions: tuple[DirectionRecord, ...],
    *,
    merge_threshold: float = 0.38,
    min_feature_count: int = 8,
) -> OthersDiscovery:
    if not 0 <= merge_threshold <= 1:
        raise ValueError("merge_threshold must be between zero and one")
    if min_feature_count < 1:
        raise ValueError("min_feature_count must be positive")
    others_ids = tuple(
        sorted(
            result.analysis_unit_id
            for result in classifications
            if result.terminal == ClassificationTerminal.OTHERS
        )
    )
    if len(others_ids) != len(set(others_ids)):
        raise ValueError("duplicate Others classification")
    direction_by_id = {record.analysis_unit_id: record for record in directions}
    if len(direction_by_id) != len(directions):
        raise ValueError("duplicate direction record")
    missing = sorted(set(others_ids) - set(direction_by_id))
    if missing:
        raise ValueError(f"Others members are missing direction records: {missing}")
    records = {member_id: direction_by_id[member_id] for member_id in others_ids}
    invalid = sorted(
        member_id
        for member_id, record in records.items()
        if record.status != DirectionStatus.AVAILABLE or not record.evidence_sufficient
    )
    if invalid:
        raise ValueError(f"Others members require available direction evidence: {invalid}")

    vectors = {member_id: _feature_vector(record) for member_id, record in records.items()}
    similarities = {
        (left, right): _cosine(vectors[left], vectors[right])
        for index, left in enumerate(others_ids)
        for right in others_ids[index + 1 :]
    }
    partitions = _complete_link_partition(others_ids, similarities, merge_threshold)
    clusters = tuple(
        _build_cluster(members, records, vectors, similarities, min_feature_count)
        for members in partitions
    )
    return OthersDiscovery(source_member_ids=others_ids, clusters=clusters)


def apply_others_naming(
    cluster: OthersCluster,
    proposal: OthersNamingProposal,
) -> OthersCluster:
    if proposal.cluster_id != cluster.cluster_id:
        raise ValueError("naming proposal belongs to another cluster")
    if proposal.member_ids != cluster.member_ids:
        raise ValueError("model cannot change Others cluster members")
    return cluster.model_copy(
        update={
            "name": proposal.name,
            "technical_problem": proposal.technical_problem,
            "common_mechanism": proposal.common_mechanism,
            "direction_boundary": proposal.direction_boundary,
            "keywords": proposal.keywords,
            "naming_source": "MODEL",
        }
    )


def _complete_link_partition(
    item_ids: tuple[str, ...],
    similarities: dict[tuple[str, str], float],
    threshold: float,
) -> tuple[tuple[str, ...], ...]:
    clusters = {item_id: (item_id,) for item_id in sorted(item_ids)}
    scores: dict[tuple[str, str], float] = {
        tuple(sorted((left, right))): score
        for (left, right), score in similarities.items()
    }
    queue: list[tuple[float, tuple[str, ...], str, str]] = []
    for (left, right), score in scores.items():
        heapq.heappush(queue, (-score, tuple(sorted((left, right))), left, right))
    while queue:
        negative_score, merged_members, left, right = heapq.heappop(queue)
        if left not in clusters or right not in clusters:
            continue
        score = -negative_score
        if score < threshold:
            break
        merged_key = "\x00".join(merged_members)
        merged = merged_members
        del clusters[left]
        del clusters[right]
        active = list(clusters.items())
        clusters[merged_key] = merged
        for other_key, _ in active:
            pair = tuple(sorted((merged_key, other_key)))
            new_score = min(
                scores[tuple(sorted((source, target)))]
                if source != target
                else 1.0
                for source in merged
                for target in clusters[other_key]
            )
            scores[pair] = new_score
            heapq.heappush(
                queue,
                (-new_score, tuple(sorted(merged + clusters[other_key])), pair[0], pair[1]),
            )
    return tuple(sorted(clusters.values()))


def _build_cluster(
    member_ids: tuple[str, ...],
    records: dict[str, DirectionRecord],
    vectors: dict[str, Counter[str]],
    similarities: dict[tuple[str, str], float],
    min_feature_count: int,
) -> OthersCluster:
    if len(member_ids) > 1:
        kind = OthersClusterKind.CANDIDATE
        cohesion = min(
            _pair_similarity(left, right, similarities)
            for index, left in enumerate(member_ids)
            for right in member_ids[index + 1 :]
        )
    else:
        member_id = member_ids[0]
        kind = (
            OthersClusterKind.SINGLETON
            if len(vectors[member_id]) >= min_feature_count
            else OthersClusterKind.NOISE
        )
        cohesion = 1.0
    representative = min(
        member_ids,
        key=lambda member_id: (
            -_mean_similarity(member_id, member_ids, similarities),
            member_id,
        ),
    )
    keywords = _fallback_keywords(member_ids, records)
    representative_record = records[representative]
    name = " / ".join(keywords[:3]) or representative_record.direction_summary[:300]
    cluster_id = _cluster_id(member_ids)
    return OthersCluster(
        cluster_id=cluster_id,
        kind=kind,
        member_ids=member_ids,
        representative_analysis_unit_id=representative,
        cohesion=round(cohesion, 6),
        name=name or cluster_id,
        technical_problem=representative_record.technical_problem,
        common_mechanism=representative_record.solution_mechanism,
        direction_boundary=representative_record.direction_summary,
        keywords=keywords,
    )


def _feature_vector(record: DirectionRecord) -> Counter[str]:
    fields = (
        record.technical_problem,
        record.solution_mechanism,
        record.technical_object,
        " ".join(record.application_scenarios),
        record.direction_summary,
        " ".join(record.keywords),
    )
    normalized = _normalize(" ".join(fields))
    vector: Counter[str] = Counter()
    for token in normalized.split():
        if len(token) <= 3:
            vector[f"t:{token}"] += 2
        else:
            for size in (2, 3):
                for index in range(len(token) - size + 1):
                    vector[f"n{size}:{token[index:index + size]}"] += 1
    for keyword in record.keywords:
        normalized_keyword = _normalize(keyword).replace(" ", "")
        if normalized_keyword:
            vector[f"k:{normalized_keyword}"] += 4
    return vector


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", value.lower())).strip()


def _cosine(left: Counter[str], right: Counter[str]) -> float:
    if not left or not right:
        return 0.0
    common = set(left) & set(right)
    numerator = sum(left[key] * right[key] for key in common)
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    return numerator / (left_norm * right_norm)


def _pair_similarity(
    left: str,
    right: str,
    similarities: dict[tuple[str, str], float],
) -> float:
    if left == right:
        return 1.0
    return similarities[tuple(sorted((left, right)))]


def _mean_similarity(
    member_id: str,
    member_ids: tuple[str, ...],
    similarities: dict[tuple[str, str], float],
) -> float:
    if len(member_ids) == 1:
        return 1.0
    return sum(
        _pair_similarity(member_id, other, similarities)
        for other in member_ids
        if other != member_id
    ) / (len(member_ids) - 1)


def _fallback_keywords(
    member_ids: tuple[str, ...],
    records: dict[str, DirectionRecord],
) -> tuple[str, ...]:
    counts: Counter[str] = Counter()
    display: dict[str, str] = {}
    for member_id in member_ids:
        for keyword in records[member_id].keywords:
            normalized = _normalize(keyword)
            if normalized:
                counts[normalized] += 1
                display.setdefault(normalized, keyword.strip())
    ordered = sorted(counts, key=lambda keyword: (-counts[keyword], keyword))
    return tuple(display[keyword] for keyword in ordered[:20])


def _cluster_id(member_ids: tuple[str, ...]) -> str:
    payload = f"{ALGORITHM_VERSION}|{'|'.join(member_ids)}"
    return f"OC-{hashlib.sha256(payload.encode()).hexdigest()[:16]}"


__all__ = [
    "ALGORITHM_VERSION",
    "OthersCluster",
    "OthersClusterKind",
    "OthersDiscovery",
    "OthersNamingProposal",
    "apply_others_naming",
    "discover_others_directions",
]
