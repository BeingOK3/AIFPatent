from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, Sequence

from .chunks import PatentChunk
from .config import HybridRetrievalSettings
from .lexical import LexicalHit, LexicalSearchRequest
from .vector import VectorHit, VectorIndex, VectorSearchRequest


HYBRID_RETRIEVER_VERSION = "hybrid-rrf-v1"


class HybridRetrievalError(RuntimeError):
    """Raised when fused retrieval violates query identity or frozen scope."""


class RetrievalMode(str, Enum):
    HYBRID = "HYBRID"
    LEXICAL_ONLY = "LEXICAL_ONLY"


class QuestionType(str, Enum):
    GENERAL = "GENERAL"
    CLAIM_OVERLAP = "CLAIM_OVERLAP"
    TECHNICAL_EXPLANATION = "TECHNICAL_EXPLANATION"
    NOVELTY = "NOVELTY"
    DESIGN_AROUND = "DESIGN_AROUND"


@dataclass(frozen=True)
class HybridSearchRequest:
    query_id: str
    text: str
    allowed_version_ids: tuple[str, ...]
    semantic_embedding: tuple[float, ...] | None = None
    embedding_profile_id: str | None = None
    section_types: tuple[str, ...] | None = None
    question_type: QuestionType = QuestionType.GENERAL
    ensure_version_diversity: bool = False
    limit: int | None = None

    def __post_init__(self) -> None:
        if not self.query_id.strip() or not self.text.strip():
            raise ValueError("hybrid query ID and text must not be empty")
        if not self.allowed_version_ids:
            raise ValueError("hybrid search requires allowed Version IDs")
        if len(set(self.allowed_version_ids)) != len(self.allowed_version_ids):
            raise ValueError("allowed Version IDs must be unique")
        if (self.semantic_embedding is None) != (self.embedding_profile_id is None):
            raise ValueError("semantic embedding and Profile ID must be provided together")
        if self.limit is not None and not 1 <= self.limit <= 30:
            raise ValueError("hybrid final limit must be between 1 and 30")


@dataclass(frozen=True)
class HybridHit:
    chunk: PatentChunk
    lexical_rank: int | None
    vector_rank: int | None
    rrf_score: float
    section_weight: float
    final_score: float
    sources: tuple[str, ...]


@dataclass(frozen=True)
class HybridSearchResult:
    query_id: str
    mode: RetrievalMode
    retriever_version: str
    hits: tuple[HybridHit, ...]
    limitations: tuple[str, ...]


class LexicalSearch(Protocol):
    async def search(self, request: LexicalSearchRequest) -> Sequence[LexicalHit]: ...


@dataclass
class _Candidate:
    chunk: PatentChunk
    lexical_rank: int | None = None
    vector_rank: int | None = None


class HybridRetriever:
    """Fuse lexical and vector ranks without mixing incomparable raw scores."""

    def __init__(
        self,
        lexical: LexicalSearch,
        settings: HybridRetrievalSettings,
        *,
        vector: VectorIndex | None = None,
    ) -> None:
        self.lexical = lexical
        self.vector = vector
        self.settings = settings

    async def search(self, request: HybridSearchRequest) -> HybridSearchResult:
        final_limit = request.limit or self.settings.final_limit
        lexical_hits = tuple(
            await self.lexical.search(
                LexicalSearchRequest(
                    query_id=request.query_id,
                    text=request.text,
                    allowed_version_ids=request.allowed_version_ids,
                    section_types=request.section_types,
                    limit=self.settings.lexical_limit,
                )
            )
        )
        self._validate_lexical(lexical_hits, request)

        vector_hits: tuple[VectorHit, ...] = ()
        limitations: list[str] = []
        if (
            self.vector is not None
            and request.semantic_embedding is not None
            and request.embedding_profile_id is not None
        ):
            vector_hits = tuple(
                await self.vector.search(
                    VectorSearchRequest(
                        query_id=request.query_id,
                        embedding=request.semantic_embedding,
                        profile_id=request.embedding_profile_id,
                        allowed_version_ids=request.allowed_version_ids,
                        section_types=request.section_types,
                        limit=self.settings.vector_limit,
                    )
                )
            )
            self._validate_vector(vector_hits, request)
            if vector_hits:
                mode = RetrievalMode.HYBRID
            else:
                mode = RetrievalMode.LEXICAL_ONLY
                limitations.extend(("VECTOR_NO_HITS", "LEXICAL_ONLY"))
        else:
            mode = RetrievalMode.LEXICAL_ONLY
            limitations.append("LEXICAL_ONLY")

        candidates: dict[str, _Candidate] = {}
        for hit in lexical_hits:
            candidates.setdefault(hit.chunk.chunk_id, _Candidate(hit.chunk)).lexical_rank = hit.rank
        for hit in vector_hits:
            candidate = candidates.setdefault(hit.chunk.chunk_id, _Candidate(hit.chunk))
            if candidate.chunk != hit.chunk:
                raise HybridRetrievalError("retrievers returned conflicting Chunk content")
            candidate.vector_rank = hit.rank

        fused = [self._score(item, request.question_type) for item in candidates.values()]
        fused.sort(key=lambda item: (-item.final_score, item.chunk.chunk_id))
        deduplicated = self._deduplicate_text(fused)
        selected = self._diversify(
            deduplicated,
            request.allowed_version_ids,
            limit=final_limit,
            ensure_versions=request.ensure_version_diversity,
        )
        return HybridSearchResult(
            query_id=request.query_id,
            mode=mode,
            retriever_version=HYBRID_RETRIEVER_VERSION,
            hits=tuple(selected),
            limitations=tuple(limitations),
        )

    def _score(self, candidate: _Candidate, question_type: QuestionType) -> HybridHit:
        score = 0.0
        sources = []
        if candidate.lexical_rank is not None:
            score += 1.0 / (self.settings.rrf_k + candidate.lexical_rank)
            sources.append("lexical")
        if candidate.vector_rank is not None:
            score += 1.0 / (self.settings.rrf_k + candidate.vector_rank)
            sources.append("vector")
        weight = self._section_weight(candidate.chunk, question_type)
        return HybridHit(
            chunk=candidate.chunk,
            lexical_rank=candidate.lexical_rank,
            vector_rank=candidate.vector_rank,
            rrf_score=score,
            section_weight=weight,
            final_score=score * weight,
            sources=tuple(sources),
        )

    @staticmethod
    def _section_weight(chunk: PatentChunk, question_type: QuestionType) -> float:
        independent = chunk.section_type == "claims" and (
            chunk.claim_kind == "independent" or chunk.claim_number == 1
        )
        dependent = chunk.section_type == "claims" and not independent
        weights = {
            QuestionType.CLAIM_OVERLAP: {
                "independent": 1.30, "dependent": 1.18,
                "description": 1.05, "abstract": 0.95,
            },
            QuestionType.TECHNICAL_EXPLANATION: {
                "independent": 1.00, "dependent": 1.00,
                "description": 1.25, "abstract": 1.10,
            },
            QuestionType.NOVELTY: {
                "independent": 1.30, "dependent": 1.15,
                "description": 1.08, "abstract": 1.05,
            },
            QuestionType.DESIGN_AROUND: {
                "independent": 1.22, "dependent": 1.18,
                "description": 1.16, "abstract": 0.95,
            },
        }
        if question_type == QuestionType.GENERAL:
            return 1.0
        key = "independent" if independent else "dependent" if dependent else chunk.section_type
        return weights[question_type].get(key, 1.0)

    @staticmethod
    def _deduplicate_text(hits: list[HybridHit]) -> list[HybridHit]:
        selected: list[HybridHit] = []
        seen = set()
        for hit in hits:
            if hit.chunk.text_hash not in seen:
                seen.add(hit.chunk.text_hash)
                selected.append(hit)
        return selected

    def _diversify(
        self,
        hits: list[HybridHit],
        version_order: tuple[str, ...],
        *,
        limit: int,
        ensure_versions: bool,
    ) -> list[HybridHit]:
        selected: list[HybridHit] = []
        selected_ids = set()
        version_counts: Counter[str] = Counter()
        label_counts: Counter[tuple[str, str]] = Counter()

        def append(hit: HybridHit) -> bool:
            version_id = hit.chunk.version_id
            label_key = (version_id, hit.chunk.section_label)
            if hit.chunk.chunk_id in selected_ids:
                return False
            if version_counts[version_id] >= self.settings.max_chunks_per_version:
                return False
            if label_counts[label_key] >= self.settings.max_chunks_per_section_label:
                return False
            selected.append(hit)
            selected_ids.add(hit.chunk.chunk_id)
            version_counts[version_id] += 1
            label_counts[label_key] += 1
            return True

        available_versions = {
            hit.chunk.version_id for hit in hits if hit.chunk.version_id in version_order
        }
        if ensure_versions and len(available_versions) <= limit:
            for version_id in version_order:
                best = next((hit for hit in hits if hit.chunk.version_id == version_id), None)
                if best is not None:
                    append(best)
        for hit in hits:
            if len(selected) >= limit:
                break
            append(hit)
        return selected

    @staticmethod
    def _validate_lexical(
        hits: tuple[LexicalHit, ...], request: HybridSearchRequest
    ) -> None:
        for hit in hits:
            if hit.query_id != request.query_id or hit.rank < 1:
                raise HybridRetrievalError("lexical hit identity or rank is invalid")
            HybridRetriever._validate_chunk_scope(hit.chunk, request)

    @staticmethod
    def _validate_vector(
        hits: tuple[VectorHit, ...], request: HybridSearchRequest
    ) -> None:
        for hit in hits:
            if hit.query_id != request.query_id or hit.rank < 1:
                raise HybridRetrievalError("vector hit identity or rank is invalid")
            HybridRetriever._validate_chunk_scope(hit.chunk, request)

    @staticmethod
    def _validate_chunk_scope(chunk: PatentChunk, request: HybridSearchRequest) -> None:
        if chunk.version_id not in request.allowed_version_ids:
            raise HybridRetrievalError("retrieval hit escaped the frozen Version scope")
        if request.section_types and chunk.section_type not in request.section_types:
            raise HybridRetrievalError("retrieval hit escaped the requested section scope")


__all__ = [
    "HYBRID_RETRIEVER_VERSION",
    "HybridHit",
    "HybridRetrievalError",
    "HybridRetriever",
    "HybridSearchRequest",
    "HybridSearchResult",
    "QuestionType",
    "RetrievalMode",
]
