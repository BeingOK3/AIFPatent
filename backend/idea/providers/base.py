from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchQuery(ContractModel):
    query_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    language: Literal["zh", "en", "mixed"] = "en"
    round_number: int = Field(ge=1)
    limit: int = Field(ge=1, le=500)
    query_type: Literal[
        "technical_means",
        "problem_effect",
        "classification",
        "distinguishing_feature",
        "publication_number",
    ]
    material_types: list[Literal["patent", "paper", "standard", "whitepaper"]] = [
        "patent"
    ]
    countries: list[str] = []


class FetchRequest(ContractModel):
    request_id: str = Field(min_length=1)
    publication_number: str | None = None
    url: str | None = None
    language: str = "en"
    include_description: bool = True

    @model_validator(mode="after")
    def require_identifier(self) -> "FetchRequest":
        if not self.publication_number and not self.url:
            raise ValueError("publication_number or url is required")
        return self


class SearchHit(ContractModel):
    provider: str = Field(min_length=1)
    provider_rank: int = Field(ge=1)
    title: str = ""
    url: str = ""
    publication_number: str | None = None
    application_number: str | None = None
    family_id: str | None = None
    snippet: str = ""
    priority_date: str | None = None
    filing_date: str | None = None
    publication_date: str | None = None
    assignee: str | None = None
    raw: dict[str, Any] = {}

    @model_validator(mode="after")
    def require_traceable_identity(self) -> "SearchHit":
        if not self.publication_number and not self.url:
            raise ValueError("search hit must have publication_number or url")
        return self


class FetchedDocument(ContractModel):
    provider: str = Field(min_length=1)
    publication_number: str = Field(min_length=1)
    application_number: str | None = None
    family_id: str | None = None
    title: str = ""
    assignee: str | None = None
    assignees: list[str] = []
    inventors: list[str] = []
    priority_date: str | None = None
    filing_date: str | None = None
    publication_date: str | None = None
    grant_date: str | None = None
    language: str = "en"
    url: str
    abstract_text: str = ""
    claims_text: str = ""
    description_text: str = ""
    section_spans: dict[str, list[dict[str, Any]]] = {}
    raw_metadata: dict[str, Any] = {}


class ProviderStatus(StrEnum):
    SUCCESS = "SUCCESS"
    EMPTY = "EMPTY"
    TIMEOUT = "TIMEOUT"
    ERROR = "ERROR"
    CONTRACT_ERROR = "CONTRACT_ERROR"
    DISABLED = "DISABLED"


class PageStopReason(StrEnum):
    MORE_AVAILABLE = "MORE_AVAILABLE"
    QUERY_EXHAUSTED = "QUERY_EXHAUSTED"
    PROVIDER_HARD_LIMIT = "PROVIDER_HARD_LIMIT"


class SearchPage(ContractModel):
    hits: list[SearchHit] = Field(max_length=100)
    next_cursor: str | None = Field(default=None, max_length=100)
    page_number: int = Field(ge=1)
    reported_total_results: int | None = Field(default=None, ge=0)
    reported_total_pages: int | None = Field(default=None, ge=0)
    provider_request_id: str = Field(min_length=1, max_length=300)
    stop_reason: PageStopReason

    @model_validator(mode="after")
    def cursor_matches_stop_reason(self) -> "SearchPage":
        if self.next_cursor and self.stop_reason != PageStopReason.MORE_AVAILABLE:
            raise ValueError("a next cursor requires MORE_AVAILABLE")
        if not self.next_cursor and self.stop_reason == PageStopReason.MORE_AVAILABLE:
            raise ValueError("MORE_AVAILABLE requires a next cursor")
        return self


@runtime_checkable
class PagedSearchProvider(Protocol):
    name: str

    async def search_page(
        self,
        query: SearchQuery,
        cursor: str | None,
    ) -> SearchPage: ...


class ProviderResult(ContractModel):
    provider: str
    operation: Literal["search", "fetch"]
    request_id: str
    status: ProviderStatus
    duration_ms: int = Field(ge=0)
    hits: list[SearchHit] = []
    document: FetchedDocument | None = None
    error_code: str | None = None
    error_message: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.status in {ProviderStatus.SUCCESS, ProviderStatus.EMPTY}


class SearchProvider(ABC):
    name: str

    @abstractmethod
    async def search(self, query: SearchQuery) -> list[SearchHit]:
        raise NotImplementedError

    @abstractmethod
    async def fetch(self, request: FetchRequest) -> FetchedDocument:
        raise NotImplementedError


class ProviderRunner:
    """Turns real provider execution into auditable, fail-closed results."""

    async def search(
        self, provider: SearchProvider, query: SearchQuery, *, timeout_seconds: float
    ) -> ProviderResult:
        started = time.monotonic()
        try:
            hits = await asyncio.wait_for(provider.search(query), timeout=timeout_seconds)
            self._validate_hits(provider.name, hits, query.limit)
            status = ProviderStatus.SUCCESS if hits else ProviderStatus.EMPTY
            return ProviderResult(
                provider=provider.name,
                operation="search",
                request_id=query.query_id,
                status=status,
                duration_ms=self._duration(started),
                hits=hits,
            )
        except TimeoutError:
            return self._failure(
                provider.name,
                "search",
                query.query_id,
                ProviderStatus.TIMEOUT,
                "PROVIDER_TIMEOUT",
                "provider search timed out",
                started,
            )
        except (ValueError, TypeError) as exc:
            return self._failure(
                provider.name,
                "search",
                query.query_id,
                ProviderStatus.CONTRACT_ERROR,
                "PROVIDER_CONTRACT_ERROR",
                str(exc),
                started,
            )
        except Exception as exc:
            return self._failure(
                provider.name,
                "search",
                query.query_id,
                ProviderStatus.ERROR,
                getattr(exc, "error_code", type(exc).__name__),
                str(exc),
                started,
            )

    async def fetch(
        self, provider: SearchProvider, request: FetchRequest, *, timeout_seconds: float
    ) -> ProviderResult:
        started = time.monotonic()
        try:
            document = await asyncio.wait_for(provider.fetch(request), timeout=timeout_seconds)
            if document.provider != provider.name:
                raise ValueError("fetched document provider does not match executor")
            if request.publication_number and not document.publication_number:
                raise ValueError("fetched document has no publication number")
            if request.publication_number and self._identifier(request.publication_number) != self._identifier(
                document.publication_number
            ):
                raise ValueError("fetched publication number does not match request")
            return ProviderResult(
                provider=provider.name,
                operation="fetch",
                request_id=request.request_id,
                status=ProviderStatus.SUCCESS,
                duration_ms=self._duration(started),
                document=document,
            )
        except TimeoutError:
            return self._failure(
                provider.name,
                "fetch",
                request.request_id,
                ProviderStatus.TIMEOUT,
                "PROVIDER_TIMEOUT",
                "provider fetch timed out",
                started,
            )
        except (ValueError, TypeError) as exc:
            return self._failure(
                provider.name,
                "fetch",
                request.request_id,
                ProviderStatus.CONTRACT_ERROR,
                "PROVIDER_CONTRACT_ERROR",
                str(exc),
                started,
            )
        except Exception as exc:
            return self._failure(
                provider.name,
                "fetch",
                request.request_id,
                ProviderStatus.ERROR,
                getattr(exc, "error_code", type(exc).__name__),
                str(exc),
                started,
            )

    @staticmethod
    def disabled(provider: str, operation: Literal["search", "fetch"], request_id: str):
        return ProviderResult(
            provider=provider,
            operation=operation,
            request_id=request_id,
            status=ProviderStatus.DISABLED,
            duration_ms=0,
            error_code="PROVIDER_DISABLED",
            error_message="provider is disabled by configuration",
        )

    @staticmethod
    def _validate_hits(provider_name: str, hits: list[SearchHit], limit: int) -> None:
        if not isinstance(hits, list):
            raise TypeError("provider search must return a list")
        if len(hits) > limit:
            raise ValueError("provider returned more hits than requested")
        ranks: set[int] = set()
        for hit in hits:
            if not isinstance(hit, SearchHit):
                raise TypeError("provider returned an unvalidated search hit")
            if hit.provider != provider_name:
                raise ValueError("search hit provider does not match executor")
            if hit.provider_rank in ranks:
                raise ValueError("provider ranks must be unique")
            ranks.add(hit.provider_rank)

    @staticmethod
    def _failure(
        provider: str,
        operation: Literal["search", "fetch"],
        request_id: str,
        status: ProviderStatus,
        code: str,
        message: str,
        started: float,
    ) -> ProviderResult:
        return ProviderResult(
            provider=provider,
            operation=operation,
            request_id=request_id,
            status=status,
            duration_ms=ProviderRunner._duration(started),
            error_code=code,
            error_message=message[:1000],
        )

    @staticmethod
    def _duration(started: float) -> int:
        return max(0, round((time.monotonic() - started) * 1000))

    @staticmethod
    def _identifier(value: str) -> str:
        return "".join(character for character in value.upper() if character.isalnum())
