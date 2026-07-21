from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol, Sequence

from .agent_schemas import IdeaFeature
from .chunks import PatentChunk
from .lexical import LexicalHit, LexicalSearchRequest


class ReportRetrievalError(RuntimeError):
    """Raised when the frozen first-report retrieval scope cannot be trusted."""


@dataclass(frozen=True)
class ReportDocumentScope:
    document_id: str
    version_id: str
    publication_number: str

    def __post_init__(self) -> None:
        if not all(value.strip() for value in self.__dict__.values()):
            raise ValueError("report document scope identifiers must not be empty")


@dataclass(frozen=True)
class ReportRetrievalQuery:
    query_id: str
    feature_id: str
    feature_text: str
    document_id: str
    version_id: str
    publication_number: str
    hit_count: int


@dataclass(frozen=True)
class ReportRetrievalSelection:
    feature_id: str
    document_id: str
    version_id: str
    publication_number: str
    hit: LexicalHit
    selection_reason: str = "lexical"
    selected_for_context: bool = True


@dataclass(frozen=True)
class ReportRetrievalResult:
    run_id: str
    retriever_version: str
    corpus_version_ids: tuple[str, ...]
    queries: tuple[ReportRetrievalQuery, ...]
    selections: tuple[ReportRetrievalSelection, ...]
    limitations: tuple[str, ...] = ()


class ReportScopeRepository(Protocol):
    async def prepare_features(
        self, run_id: str, features: tuple[IdeaFeature, ...]
    ) -> None: ...

    async def load_ready_deep_reviewed(
        self, run_id: str
    ) -> tuple[ReportDocumentScope, ...]: ...

    async def persist(self, result: ReportRetrievalResult) -> None: ...


class LexicalSearch(Protocol):
    async def search(self, request: LexicalSearchRequest) -> Sequence[LexicalHit]: ...


class ReportChunkSource(Protocol):
    async def list_for_versions(
        self, version_ids: tuple[str, ...]
    ) -> Sequence[PatentChunk]: ...


class InitialReportRetriever:
    """Runs an auditable lexical query for every required Feature × Version pair."""

    def __init__(
        self,
        scope_repository: ReportScopeRepository,
        lexical_search: LexicalSearch,
        *,
        chunk_repository: ReportChunkSource | None = None,
        per_pair_limit: int = 8,
        retriever_version: str = "initial-report-lexical-v1",
    ) -> None:
        if not 1 <= per_pair_limit <= 50:
            raise ValueError("per-pair lexical limit must be between 1 and 50")
        if not retriever_version.strip():
            raise ValueError("retriever version must not be empty")
        self.scope_repository = scope_repository
        self.lexical_search = lexical_search
        self.chunk_repository = chunk_repository
        self.per_pair_limit = per_pair_limit
        self.retriever_version = retriever_version

    async def retrieve(
        self, *, run_id: str, features: Sequence[IdeaFeature]
    ) -> ReportRetrievalResult:
        if not run_id.strip():
            raise ReportRetrievalError("run ID must not be empty")
        required = tuple(feature for feature in features if feature.required)
        if not required:
            raise ReportRetrievalError("initial report requires at least one required feature")
        feature_ids = [feature.feature_id for feature in required]
        if len(set(feature_ids)) != len(feature_ids):
            raise ReportRetrievalError("required feature IDs must be unique")

        await self.scope_repository.prepare_features(run_id, required)
        scopes = tuple(await self.scope_repository.load_ready_deep_reviewed(run_id))
        if not scopes:
            raise ReportRetrievalError(
                "initial report requires a READY deep-reviewed Corpus Version"
            )
        version_ids = [scope.version_id for scope in scopes]
        if len(set(version_ids)) != len(version_ids):
            raise ReportRetrievalError("report scope must contain one unique Version per document")
        document_ids = [scope.document_id for scope in scopes]
        if len(set(document_ids)) != len(document_ids):
            raise ReportRetrievalError("report scope document IDs must be unique")

        queries: list[ReportRetrievalQuery] = []
        selections: list[ReportRetrievalSelection] = []
        limitations: list[str] = []
        chunks_by_version: dict[str, tuple[PatentChunk, ...]] = {}
        if self.chunk_repository is not None:
            loaded = tuple(
                await self.chunk_repository.list_for_versions(tuple(version_ids))
            )
            unexpected = {item.version_id for item in loaded} - set(version_ids)
            if unexpected:
                raise ReportRetrievalError("Chunk repository escaped the frozen Version scope")
            for scope in scopes:
                scoped = tuple(item for item in loaded if item.version_id == scope.version_id)
                if any(item.publication_number != scope.publication_number for item in scoped):
                    raise ReportRetrievalError("Chunk publication does not match frozen scope")
                chunks_by_version[scope.version_id] = scoped
        for feature in required:
            for scope in scopes:
                query_id = self._query_id(run_id, feature.feature_id, scope.version_id)
                request = LexicalSearchRequest(
                    query_id=query_id,
                    text=feature.feature_text,
                    allowed_version_ids=(scope.version_id,),
                    limit=self.per_pair_limit,
                )
                hits = tuple(await self.lexical_search.search(request))
                for hit in hits:
                    self._validate_hit(hit, request, scope)
                    selections.append(
                        ReportRetrievalSelection(
                            feature_id=feature.feature_id,
                            document_id=scope.document_id,
                            version_id=scope.version_id,
                            publication_number=scope.publication_number,
                            hit=hit,
                        )
                    )
                if self.chunk_repository is not None:
                    self._append_forced_evidence(
                        feature_id=feature.feature_id,
                        scope=scope,
                        query_id=query_id,
                        chunks=chunks_by_version[scope.version_id],
                        lexical_hits=hits,
                        selections=selections,
                        limitations=limitations,
                    )
                queries.append(
                    ReportRetrievalQuery(
                        query_id=query_id,
                        feature_id=feature.feature_id,
                        feature_text=feature.feature_text,
                        document_id=scope.document_id,
                        version_id=scope.version_id,
                        publication_number=scope.publication_number,
                        hit_count=len(hits),
                    )
                )

        expected_pairs = {
            (feature.feature_id, scope.version_id)
            for feature in required
            for scope in scopes
        }
        actual_pairs = {(query.feature_id, query.version_id) for query in queries}
        if actual_pairs != expected_pairs:
            raise ReportRetrievalError("Feature × Version retrieval matrix is incomplete")

        result = ReportRetrievalResult(
            run_id=run_id,
            retriever_version=self.retriever_version,
            corpus_version_ids=tuple(version_ids),
            queries=tuple(queries),
            selections=tuple(selections),
            limitations=tuple(dict.fromkeys(limitations)),
        )
        await self.scope_repository.persist(result)
        return result

    @staticmethod
    def _query_id(run_id: str, feature_id: str, version_id: str) -> str:
        digest = hashlib.sha256(
            f"{run_id}|{feature_id}|{version_id}|lexical".encode("utf-8")
        ).hexdigest()[:20]
        return f"RQ-{digest}"

    @staticmethod
    def _validate_hit(
        hit: LexicalHit,
        request: LexicalSearchRequest,
        scope: ReportDocumentScope,
    ) -> None:
        if hit.query_id != request.query_id:
            raise ReportRetrievalError("lexical hit query ID does not match its request")
        if hit.chunk.version_id != scope.version_id:
            raise ReportRetrievalError("lexical hit escaped the frozen Version scope")
        if hit.chunk.publication_number != scope.publication_number:
            raise ReportRetrievalError("lexical hit publication does not match its Version scope")
        if hit.rank < 1:
            raise ReportRetrievalError("lexical hit rank must be positive")

    @classmethod
    def _append_forced_evidence(
        cls,
        *,
        feature_id: str,
        scope: ReportDocumentScope,
        query_id: str,
        chunks: tuple[PatentChunk, ...],
        lexical_hits: tuple[LexicalHit, ...],
        selections: list[ReportRetrievalSelection],
        limitations: list[str],
    ) -> None:
        abstracts = tuple(item for item in chunks if item.section_type == "abstract")
        independent = tuple(
            item
            for item in chunks
            if item.section_type == "claims" and item.claim_kind == "independent"
        )
        if not abstracts:
            limitations.append(f"MISSING_ABSTRACT:{scope.version_id}")
        if not independent:
            limitations.append(f"MISSING_INDEPENDENT_CLAIM:{scope.version_id}")

        existing = {
            (item.feature_id, item.version_id, item.hit.chunk.chunk_id, item.selection_reason)
            for item in selections
        }

        def append(chunk: PatentChunk, reason: str) -> None:
            key = (feature_id, scope.version_id, chunk.chunk_id, reason)
            if key in existing:
                return
            selections.append(
                ReportRetrievalSelection(
                    feature_id=feature_id,
                    document_id=scope.document_id,
                    version_id=scope.version_id,
                    publication_number=scope.publication_number,
                    hit=LexicalHit(
                        query_id=query_id,
                        rank=1,
                        lexical_score=0.0,
                        match_kind=reason,
                        chunk=chunk,
                    ),
                    selection_reason=reason,
                )
            )
            existing.add(key)

        for item in (*abstracts, *independent):
            append(item, "forced_abstract" if item.section_type == "abstract" else "forced_claim")

        claims = {
            item.claim_number: item
            for item in chunks
            if item.section_type == "claims" and item.claim_number is not None
        }

        def add_parents(item: PatentChunk, path: tuple[int, ...]) -> None:
            for parent_number in item.parent_claim_numbers:
                if parent_number in path:
                    raise ReportRetrievalError(
                        f"claim parent cycle in {scope.version_id}: {path + (parent_number,)}"
                    )
                parent = claims.get(parent_number)
                if parent is None:
                    raise ReportRetrievalError(
                        f"missing parent claim {parent_number} in {scope.version_id}"
                    )
                append(parent, "forced_claim")
                add_parents(parent, path + (parent_number,))

        for hit in lexical_hits:
            if hit.chunk.section_type == "claims" and hit.chunk.parent_claim_numbers:
                current = hit.chunk.claim_number
                add_parents(hit.chunk, (() if current is None else (current,)))


__all__ = [
    "InitialReportRetriever",
    "ReportDocumentScope",
    "ReportRetrievalError",
    "ReportRetrievalQuery",
    "ReportRetrievalResult",
    "ReportRetrievalSelection",
    "ReportScopeRepository",
    "ReportChunkSource",
]
