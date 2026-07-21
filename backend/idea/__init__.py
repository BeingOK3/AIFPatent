"""Deterministic IDEA review harness."""

from .config import AppConfig, ConfigError, load_config

from .corpus import (
    CorpusError,
    CorpusIngestResult,
    CorpusRunLink,
    CorpusVersion,
    CorpusVersionSource,
    PatentCorpusIngestService,
    PatentCorpusService,
)
from .chunks import PatentChunk, PatentChunker
from .context import AssembledModelContext, ContextAssembler, ContextAssemblyError
from .context_adapter import LangChainAdapterUnavailable, to_langchain_messages, to_message_dicts
from .s3_object_store import S3ObjectStore
from .postgres_corpus import (
    PostgreSQLCorpusError,
    PostgreSQLCorpusPrerequisiteRepository,
    PostgreSQLCorpusRunLinkRepository,
    PostgreSQLCorpusVersionSourceRepository,
    PostgreSQLCorpusVersionRepository,
)

__all__ = [
    "AppConfig",
    "ConfigError",
    "CorpusError",
    "CorpusIngestResult",
    "CorpusRunLink",
    "CorpusVersion",
    "CorpusVersionSource",
    "PatentCorpusIngestService",
    "PatentCorpusService",
    "PostgreSQLCorpusError",
    "PostgreSQLCorpusPrerequisiteRepository",
    "PostgreSQLCorpusRunLinkRepository",
    "PostgreSQLCorpusVersionRepository",
    "PostgreSQLCorpusVersionSourceRepository",
    "S3ObjectStore",
    "PatentChunk",
    "PatentChunker",
    "AssembledModelContext",
    "ContextAssembler",
    "ContextAssemblyError",
    "LangChainAdapterUnavailable",
    "to_langchain_messages",
    "to_message_dicts",
    "load_config",
]
