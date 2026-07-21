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
from .chunks import (
    ChunkPersistenceError,
    PatentChunk,
    PatentChunker,
    PatentChunkPersistenceService,
    PatentChunkRepository,
)
from .corpus_migration import (
    CorpusMigrationError,
    HistoricalCorpusCandidate,
    HistoricalCorpusFetcher,
    HistoricalCorpusMigrator,
    MigrationOutcome,
    MigrationReport,
    MigrationStatus,
    RetrievalCorpusFetcher,
)
from .context import AssembledModelContext, ContextAssembler, ContextAssemblyError
from .context_adapter import LangChainAdapterUnavailable, to_langchain_messages, to_message_dicts
from .lexical import (
    LEXICAL_TOKENIZER_VERSION,
    LexicalHit,
    LexicalSearchRequest,
    LexicalTerms,
    lexical_query_terms,
    lexical_search_terms,
    normalize_lexical_text,
)
from .postgres_lexical import PostgreSQLLexicalSearchRepository
from .postgres_report import PostgreSQLReportScopeRepository
from .report_retrieval import (
    InitialReportRetriever,
    ReportDocumentScope,
    ReportRetrievalError,
    ReportRetrievalQuery,
    ReportRetrievalResult,
    ReportRetrievalSelection,
)
from .s3_object_store import S3ObjectStore
from .postgres_corpus import (
    PostgreSQLCorpusError,
    PostgreSQLCorpusPrerequisiteRepository,
    PostgreSQLCorpusRunLinkRepository,
    PostgreSQLCorpusVersionSourceRepository,
    PostgreSQLCorpusVersionRepository,
    PostgreSQLPatentChunkRepository,
)

__all__ = [
    "AppConfig",
    "ConfigError",
    "CorpusError",
    "CorpusIngestResult",
    "CorpusMigrationError",
    "CorpusRunLink",
    "CorpusVersion",
    "CorpusVersionSource",
    "PatentCorpusIngestService",
    "PatentCorpusService",
    "HistoricalCorpusCandidate",
    "HistoricalCorpusFetcher",
    "HistoricalCorpusMigrator",
    "MigrationOutcome",
    "MigrationReport",
    "MigrationStatus",
    "RetrievalCorpusFetcher",
    "PostgreSQLCorpusError",
    "PostgreSQLCorpusPrerequisiteRepository",
    "PostgreSQLCorpusRunLinkRepository",
    "PostgreSQLCorpusVersionRepository",
    "PostgreSQLCorpusVersionSourceRepository",
    "PostgreSQLPatentChunkRepository",
    "S3ObjectStore",
    "PatentChunk",
    "PatentChunker",
    "PatentChunkPersistenceService",
    "PatentChunkRepository",
    "ChunkPersistenceError",
    "AssembledModelContext",
    "ContextAssembler",
    "ContextAssemblyError",
    "LangChainAdapterUnavailable",
    "LEXICAL_TOKENIZER_VERSION",
    "LexicalHit",
    "LexicalSearchRequest",
    "LexicalTerms",
    "PostgreSQLLexicalSearchRepository",
    "PostgreSQLReportScopeRepository",
    "InitialReportRetriever",
    "ReportDocumentScope",
    "ReportRetrievalError",
    "ReportRetrievalQuery",
    "ReportRetrievalResult",
    "ReportRetrievalSelection",
    "lexical_query_terms",
    "lexical_search_terms",
    "normalize_lexical_text",
    "to_langchain_messages",
    "to_message_dicts",
    "load_config",
]
