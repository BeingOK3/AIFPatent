from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Protocol, Sequence

from .corpus import CorpusVersion
from .providers import FetchedDocument


_CLAIM_START = re.compile(r"(?m)^\s*(\d{1,4})\s*[.、)]\s*")


@dataclass(frozen=True)
class PatentChunk:
    chunk_id: str
    version_id: str
    publication_number: str
    section_type: str
    section_label: str
    claim_number: int | None
    claim_kind: str | None
    parent_claim_numbers: tuple[int, ...]
    start_offset: int
    end_offset: int
    text: str
    text_hash: str
    token_count: int
    chunker_version: str


class PatentChunkRepository(Protocol):
    async def put_many_if_absent(
        self, chunks: tuple[PatentChunk, ...]
    ) -> tuple[PatentChunk, ...]: ...


class ChunkEmbeddingIndexer(Protocol):
    async def index_chunks(self, chunks: Sequence[Any]) -> tuple[Any, ...]: ...

    async def activate_for_chunks(self, chunk_ids: Sequence[str]) -> None: ...


class ChunkPersistenceError(RuntimeError):
    """Raised when a complete deterministic Chunk set cannot be persisted."""


class PatentChunker:
    """Produce stable, structure-aware chunks without changing source text."""

    def __init__(self, *, chunker_version: str = "claims-paragraphs-v1") -> None:
        if not chunker_version.strip():
            raise ValueError("chunker_version must not be empty")
        self.chunker_version = chunker_version

    def chunk(self, version: CorpusVersion, document: FetchedDocument) -> tuple[PatentChunk, ...]:
        if version.version_id == "" or version.publication_number != document.publication_number:
            raise ValueError("document does not belong to corpus version")
        chunks: list[PatentChunk] = []
        chunks.extend(self._single_section(version, "abstract", document.abstract_text))
        chunks.extend(self._claims(version, document.claims_text))
        chunks.extend(self._paragraphs(version, "description", document.description_text))
        return tuple(chunks)

    def _single_section(self, version: CorpusVersion, section: str, text: str) -> list[PatentChunk]:
        normalized = text.strip()
        if not normalized:
            return []
        return [self._make(version, section, section, None, None, (), 0, len(normalized), normalized)]

    def _claims(self, version: CorpusVersion, text: str) -> list[PatentChunk]:
        source = text.strip()
        if not source:
            return []
        matches = list(_CLAIM_START.finditer(source))
        if not matches:
            return [self._make(version, "claims", "claims", None, None, (), 0, len(source), source)]
        chunks: list[PatentChunk] = []
        for index, match in enumerate(matches):
            start = match.start()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
            claim_number = int(match.group(1))
            claim_text = source[start:end].strip()
            parent = self._parent_claims(claim_text, claim_number)
            kind = "independent" if not parent else "dependent"
            chunks.append(
                self._make(
                    version,
                    "claims",
                    f"claim-{claim_number}",
                    claim_number,
                    kind,
                    parent,
                    start,
                    end,
                    claim_text,
                )
            )
        return chunks

    @staticmethod
    def _parent_claims(text: str, claim_number: int) -> tuple[int, ...]:
        matches = [int(value) for value in re.findall(r"(?:claim|权利要求)\s*(\d{1,4})", text, re.I)]
        return tuple(sorted(set(value for value in matches if value != claim_number)))

    def _paragraphs(self, version: CorpusVersion, section: str, text: str) -> list[PatentChunk]:
        source = text.strip()
        if not source:
            return []
        chunks: list[PatentChunk] = []
        cursor = 0
        for index, paragraph in enumerate((part.strip() for part in re.split(r"\n\s*\n", source))):
            if not paragraph:
                continue
            start = source.find(paragraph, cursor)
            end = start + len(paragraph)
            cursor = end
            chunks.append(self._make(version, section, f"paragraph-{index + 1}", None, None, (), start, end, paragraph))
        return chunks

    def _make(
        self,
        version: CorpusVersion,
        section: str,
        label: str,
        claim_number: int | None,
        claim_kind: str | None,
        parents: tuple[int, ...],
        start: int,
        end: int,
        text: str,
    ) -> PatentChunk:
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        identity = "|".join(
            (version.version_id, section, label, str(start), str(end), self.chunker_version)
        )
        chunk_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return PatentChunk(
            chunk_id=chunk_id,
            version_id=version.version_id,
            publication_number=version.publication_number,
            section_type=section,
            section_label=label,
            claim_number=claim_number,
            claim_kind=claim_kind,
            parent_claim_numbers=parents,
            start_offset=start,
            end_offset=end,
            text=text,
            text_hash=text_hash,
            token_count=len(text.split()),
            chunker_version=self.chunker_version,
        )


class PatentChunkPersistenceService:
    """Generate and durably verify all Chunks for one immutable Version."""

    def __init__(
        self,
        *,
        repository: PatentChunkRepository,
        chunker: PatentChunker | None = None,
        embedding_indexer: ChunkEmbeddingIndexer | None = None,
    ) -> None:
        self.repository = repository
        self.chunker = chunker or PatentChunker()
        self.embedding_indexer = embedding_indexer

    async def persist(
        self, version: CorpusVersion, document: FetchedDocument
    ) -> tuple[PatentChunk, ...]:
        expected = self.chunker.chunk(version, document)
        if not expected:
            raise ChunkPersistenceError("corpus version produced no durable chunks")
        persisted = await self.repository.put_many_if_absent(expected)
        if persisted != expected:
            raise ChunkPersistenceError("persisted Chunk set does not match deterministic output")
        if self.embedding_indexer is not None:
            await self.embedding_indexer.index_chunks(persisted)
            await self.embedding_indexer.activate_for_chunks(
                tuple(item.chunk_id for item in persisted)
            )
        return persisted


__all__ = [
    "ChunkPersistenceError",
    "ChunkEmbeddingIndexer",
    "PatentChunk",
    "PatentChunker",
    "PatentChunkPersistenceService",
    "PatentChunkRepository",
]
