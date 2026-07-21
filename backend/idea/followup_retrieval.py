from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from typing import Protocol

from .followup import FollowupError, FollowupTurn
from .followup_plan import FollowupPlan
from .hybrid import (
    HybridHit,
    HybridRetriever,
    HybridSearchRequest,
    HybridSearchResult,
    RetrievalMode,
)


class QueryEmbeddingProvider(Protocol):
    async def embed_query(self, text: str) -> tuple[tuple[float, ...], str]: ...


@dataclass
class _Merged:
    hit: HybridHit
    score: float
    lexical_rank: int | None
    vector_rank: int | None
    sources: set[str]


class MultiQueryFollowupRetriever:
    """Execute verified rewrites on one frozen allowlist and merge deterministically."""

    def __init__(
        self,
        hybrid: HybridRetriever,
        *,
        embedding: QueryEmbeddingProvider | None = None,
    ) -> None:
        self.hybrid = hybrid
        self.embedding = embedding

    async def retrieve(
        self, *, turn: FollowupTurn, plan: FollowupPlan
    ) -> HybridSearchResult:
        selected = set(plan.selected_publication_numbers)
        documents = tuple(
            item for item in turn.scope.documents if item.publication_number in selected
        )
        if len(documents) != len(selected):
            raise FollowupError("follow-up retrieval publications escaped the Turn scope")
        allowed_versions = tuple(item.version_id for item in documents)
        query_key = "|".join(
            [turn.turn_id, *allowed_versions, *plan.query_rewrites]
        )
        aggregate_id = "FQ-" + hashlib.sha256(query_key.encode()).hexdigest()[:24]
        merged: dict[str, _Merged] = {}
        limitations: list[str] = []
        modes: list[RetrievalMode] = []

        def merge_result(result: HybridSearchResult, source_tag: str) -> None:
            modes.append(result.mode)
            limitations.extend(result.limitations)
            for hit in result.hits:
                if hit.chunk.version_id not in allowed_versions:
                    raise FollowupError("Hybrid result escaped follow-up Version scope")
                current = merged.get(hit.chunk.chunk_id)
                tagged_sources = {f"{source_tag}:{source}" for source in hit.sources}
                if current is None:
                    merged[hit.chunk.chunk_id] = _Merged(
                        hit=hit,
                        score=hit.rrf_score,
                        lexical_rank=hit.lexical_rank,
                        vector_rank=hit.vector_rank,
                        sources=tagged_sources,
                    )
                else:
                    if current.hit.chunk != hit.chunk:
                        raise FollowupError("follow-up rewrites returned conflicting Chunk data")
                    current.score += hit.rrf_score
                    current.lexical_rank = self._minimum(
                        current.lexical_rank, hit.lexical_rank
                    )
                    current.vector_rank = self._minimum(
                        current.vector_rank, hit.vector_rank
                    )
                    current.sources.update(tagged_sources)

        for index, text in enumerate(plan.query_rewrites, start=1):
            vector = profile = None
            if self.embedding is not None:
                vector, profile = await self.embedding.embed_query(text)
            result = await self.hybrid.search(HybridSearchRequest(
                query_id=f"{aggregate_id}:Q{index}",
                text=text,
                allowed_version_ids=allowed_versions,
                semantic_embedding=vector,
                embedding_profile_id=profile,
                section_types=(tuple(plan.preferred_sections) or None),
                question_type=plan.question_type.as_retrieval_type(),
                ensure_version_diversity=len(allowed_versions) > 1,
                limit=self.hybrid.settings.final_limit,
            ))
            merge_result(result, f"Q{index}")

        covered_versions = {value.hit.chunk.version_id for value in merged.values()}
        for index, document in enumerate(documents, start=1):
            if document.version_id in covered_versions:
                continue
            sections = tuple(plan.preferred_sections) or ("claims",)
            seed = await self.hybrid.search(HybridSearchRequest(
                query_id=f"{aggregate_id}:D{index}",
                text=document.publication_number,
                allowed_version_ids=(document.version_id,),
                section_types=sections,
                question_type=plan.question_type.as_retrieval_type(),
                limit=min(2, self.hybrid.settings.final_limit),
            ))
            if not seed.hits and sections:
                seed = await self.hybrid.search(HybridSearchRequest(
                    query_id=f"{aggregate_id}:D{index}:ALL",
                    text=document.publication_number,
                    allowed_version_ids=(document.version_id,),
                    section_types=None,
                    question_type=plan.question_type.as_retrieval_type(),
                    limit=min(2, self.hybrid.settings.final_limit),
                ))
                limitations.append("SECTION_FILTER_FALLBACK")
            if seed.hits:
                merge_result(seed, f"D{index}:mandatory")
                limitations.append("MANDATORY_VERSION_EVIDENCE_FALLBACK")

        ranked = [
            HybridHit(
                chunk=value.hit.chunk,
                lexical_rank=value.lexical_rank,
                vector_rank=value.vector_rank,
                rrf_score=value.score,
                section_weight=value.hit.section_weight,
                final_score=value.score * value.hit.section_weight,
                sources=tuple(sorted(value.sources)),
            )
            for value in merged.values()
        ]
        ranked.sort(key=lambda item: (-item.final_score, item.chunk.chunk_id))
        ranked = self._select(ranked, allowed_versions)
        if not ranked:
            raise FollowupError("follow-up retrieval returned no in-scope evidence")
        mode = (
            RetrievalMode.HYBRID
            if modes and all(value == RetrievalMode.HYBRID for value in modes)
            else RetrievalMode.LEXICAL_ONLY
        )
        if mode == RetrievalMode.LEXICAL_ONLY:
            limitations.append("LEXICAL_ONLY")
        return HybridSearchResult(
            query_id=aggregate_id,
            mode=mode,
            retriever_version=turn.retriever_version,
            hits=tuple(ranked),
            limitations=tuple(dict.fromkeys(limitations)),
        )

    def _select(
        self, values: list[HybridHit], version_order: tuple[str, ...]
    ) -> list[HybridHit]:
        unique_text: list[HybridHit] = []
        hashes = set()
        for value in values:
            if value.chunk.text_hash not in hashes:
                hashes.add(value.chunk.text_hash)
                unique_text.append(value)
        selected: list[HybridHit] = []
        ids = set()
        version_counts: Counter[str] = Counter()
        label_counts: Counter[tuple[str, str]] = Counter()

        def add(hit: HybridHit) -> None:
            label = (hit.chunk.version_id, hit.chunk.section_label)
            if hit.chunk.chunk_id in ids:
                return
            if version_counts[hit.chunk.version_id] >= self.hybrid.settings.max_chunks_per_version:
                return
            if label_counts[label] >= self.hybrid.settings.max_chunks_per_section_label:
                return
            selected.append(hit)
            ids.add(hit.chunk.chunk_id)
            version_counts[hit.chunk.version_id] += 1
            label_counts[label] += 1

        if len(version_order) <= self.hybrid.settings.final_limit:
            for version_id in version_order:
                best = next(
                    (item for item in unique_text if item.chunk.version_id == version_id),
                    None,
                )
                if best is not None:
                    add(best)
        for value in unique_text:
            if len(selected) >= self.hybrid.settings.final_limit:
                break
            add(value)
        return selected

    @staticmethod
    def _minimum(left: int | None, right: int | None) -> int | None:
        values = [value for value in (left, right) if value is not None]
        return min(values) if values else None


__all__ = ["MultiQueryFollowupRetriever", "QueryEmbeddingProvider"]
