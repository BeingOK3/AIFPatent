from __future__ import annotations

import asyncio
import json
import re
from urllib.parse import parse_qs, urlparse
from collections.abc import Awaitable, Callable
from html.parser import HTMLParser
from typing import Any

import httpx

from ..cache import CacheStore
from ..config import SerpApiSettings
from .base import (
    FetchRequest,
    FetchedDocument,
    PageStopReason,
    SearchHit,
    SearchPage,
    SearchProvider,
    SearchQuery,
)


SerpApiTransport = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
DescriptionTransport = Callable[[str], Awaitable[str]]

_WINDOW_PATTERN = re.compile(r"\s+(after|before)=publication:(\d{8})(?=\s|$)")
_ASSIGNEE_PATTERN = re.compile(r'assignee:"([^"]+)"', re.IGNORECASE)


class SerpApiError(RuntimeError):
    def __init__(self, error_code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable


class _PlainTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = " ".join(data.split())
        if value:
            self.parts.append(value)

    def text(self) -> str:
        return "\n\n".join(self.parts)


def _join_spans(parts: list[str], label: str) -> tuple[str, list[dict[str, Any]]]:
    text_parts: list[str] = []
    spans: list[dict[str, Any]] = []
    offset = 0
    for index, raw in enumerate(parts, start=1):
        value = str(raw or "").strip()
        if not value:
            continue
        if text_parts:
            text_parts.append("\n\n")
            offset += 2
        start = offset
        text_parts.append(value)
        offset += len(value)
        spans.append(
            {
                "label": f"{label} {index}",
                "start": start,
                "end": offset,
                "text": value,
            }
        )
    return "".join(text_parts), spans


def _list_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _inventor_names(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    names: list[str] = []
    for item in value:
        if isinstance(item, dict) and item.get("name"):
            names.append(str(item["name"]).strip())
        elif isinstance(item, str) and item.strip():
            names.append(item.strip())
    return names


def _description_text(content: str) -> str:
    parser = _PlainTextParser()
    parser.feed(content)
    return parser.text()


def parse_serpapi_patent_details(
    payload: dict[str, Any],
    *,
    requested_publication: str,
    language: str,
    description_text: str = "",
) -> FetchedDocument:
    publication = str(payload.get("publication_number") or requested_publication).strip()
    if not publication:
        raise ValueError("SerpAPI details response has no publication number")
    if payload.get("type") not in {None, "patent"}:
        raise ValueError("SerpAPI details response is not a patent")
    claims_text, claim_spans = _join_spans(_list_strings(payload.get("claims")), "claim")
    description_parts = [
        part.strip() for part in re.split(r"\n\s*\n", description_text) if part.strip()
    ]
    normalized_description, description_spans = _join_spans(
        description_parts, "description"
    )
    abstract = str(payload.get("abstract") or payload.get("abstract_original") or "").strip()
    if not abstract and not claims_text and not normalized_description:
        raise ValueError("SerpAPI details response has no usable patent text")
    assignees = _list_strings(payload.get("assignees"))
    canonical_url = str(
        payload.get("main_url")
        or payload.get("full_view_url")
        or payload.get("search_metadata", {}).get("google_patents_details_url")
        or f"https://patents.google.com/patent/{publication}/{language}"
    )
    return FetchedDocument(
        provider="serpapi_google_patents",
        publication_number=publication,
        application_number=str(payload.get("application_number") or "") or None,
        family_id=str(payload.get("family_id") or "") or None,
        title=str(payload.get("title") or ""),
        assignee=assignees[0] if assignees else None,
        assignees=assignees,
        inventors=_inventor_names(payload.get("inventors")),
        priority_date=str(payload.get("priority_date") or "") or None,
        filing_date=str(payload.get("filing_date") or "") or None,
        publication_date=str(payload.get("publication_date") or "") or None,
        language=language,
        url=canonical_url,
        abstract_text=abstract,
        claims_text=claims_text,
        description_text=normalized_description,
        section_spans={
            "abstract": (
                [{"label": "abstract", "start": 0, "end": len(abstract), "text": abstract}]
                if abstract
                else []
            ),
            "claims": claim_spans,
            "description": description_spans,
        },
        raw_metadata={
            "source": "serpapi_google_patents",
            "country": payload.get("country"),
            "worldwide_applications": payload.get("worldwide_applications") or {},
            "classifications": payload.get("classifications") or [],
            "description_available": bool(normalized_description),
        },
    )


class SerpApiPatentProvider(SearchProvider):
    name = "serpapi_google_patents"

    def __init__(
        self,
        settings: SerpApiSettings,
        *,
        cache: CacheStore | None = None,
        transport: SerpApiTransport | None = None,
        description_transport: DescriptionTransport | None = None,
    ):
        self.settings = settings
        self.cache = cache
        self.transport = transport or self._http_json
        self.description_transport = description_transport or self._http_description

    def api_key(self) -> str:
        try:
            payload = json.loads(
                self.settings.api_key_file.read_text(encoding="utf-8-sig")
            )
        except FileNotFoundError as exc:
            raise SerpApiError(
                "SERPAPI_API_KEY_REQUIRED",
                "Local provider credentials file is missing",
            ) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise SerpApiError(
                "SERPAPI_CREDENTIAL_FILE_INVALID",
                "Local provider credentials file is not valid JSON",
            ) from exc
        section = payload.get("serpapi") if isinstance(payload, dict) else None
        value = str(section.get("api_key") or "").strip() if isinstance(section, dict) else ""
        if not value:
            raise SerpApiError(
                "SERPAPI_API_KEY_REQUIRED",
                "SerpAPI API key is missing from local provider credentials",
            )
        return value

    async def search(self, query: SearchQuery) -> list[SearchHit]:
        page = await self.search_page(query, cursor=None)
        return page.hits[: query.limit]

    async def search_page(
        self,
        query: SearchQuery,
        cursor: str | None,
    ) -> SearchPage:
        page_number = self._decode_page_cursor(cursor)
        query_text, window = self._extract_window(query.text)
        query_text, assignees = self._extract_assignees(query_text)
        page_size = min(100, max(10, query.limit))
        arguments: dict[str, Any] = {
            "engine": self.settings.search_engine,
            "q": query_text,
            "num": page_size,
            "patents": "true",
            "scholar": "false",
            "dups": "language",
        }
        if page_number > 1:
            arguments["page"] = page_number
        if assignees:
            arguments["assignee"] = ",".join(
                f"({name})" if "," in name else name for name in assignees
            )
        arguments.update(window)
        payload = await self._call(arguments)
        results = payload.get("organic_results") or []
        if not isinstance(results, list):
            raise ValueError("SerpAPI organic_results must be a list")
        hits: list[SearchHit] = []
        seen: set[str] = set()
        for item in results:
            if not isinstance(item, dict) or item.get("is_scholar"):
                continue
            publication = str(item.get("publication_number") or "").strip()
            url = str(item.get("patent_link") or "").strip()
            identity = publication.replace(" ", "").upper() or url
            if not identity or identity in seen:
                continue
            seen.add(identity)
            hits.append(
                SearchHit(
                    provider=self.name,
                    provider_rank=(page_number - 1) * page_size + len(hits) + 1,
                    title=str(item.get("title") or ""),
                    url=url,
                    publication_number=publication or None,
                    snippet=str(item.get("snippet") or ""),
                    priority_date=str(item.get("priority_date") or "") or None,
                    filing_date=str(item.get("filing_date") or "") or None,
                    publication_date=str(item.get("publication_date") or "") or None,
                    assignee=str(item.get("assignee") or "") or None,
                    raw={
                        "patent_id": item.get("patent_id"),
                        "country_status": item.get("country_status") or {},
                        "language": item.get("language"),
                    },
                )
            )
            if len(hits) >= page_size:
                break
        search_information = payload.get("search_information") or {}
        pagination = payload.get("serpapi_pagination") or {}
        if not isinstance(search_information, dict) or not isinstance(pagination, dict):
            raise ValueError("SerpAPI pagination metadata must be objects")
        reported_total = self._optional_count(search_information.get("total_results"))
        reported_pages = self._optional_count(
            pagination.get("total_pages") or search_information.get("total_pages")
        )
        if reported_pages is None and reported_total is not None:
            reported_pages = (reported_total + page_size - 1) // page_size
        next_page = self._next_page_number(pagination.get("next"), page_number)
        if next_page is not None:
            next_cursor = f"page:{next_page}"
            stop_reason = PageStopReason.MORE_AVAILABLE
        else:
            next_cursor = None
            consumed = (page_number - 1) * page_size + len(hits)
            stop_reason = (
                PageStopReason.PROVIDER_HARD_LIMIT
                if reported_total is not None and reported_total > consumed
                else PageStopReason.QUERY_EXHAUSTED
            )
        metadata = payload.get("search_metadata") or {}
        provider_request_id = (
            str(metadata.get("id") or "").strip()
            if isinstance(metadata, dict)
            else ""
        ) or f"serpapi-unreported:{query.query_id}:{page_number}"
        return SearchPage(
            hits=hits,
            next_cursor=next_cursor,
            page_number=page_number,
            reported_total_results=reported_total,
            reported_total_pages=reported_pages,
            provider_request_id=provider_request_id,
            stop_reason=stop_reason,
        )

    @staticmethod
    def _decode_page_cursor(cursor: str | None) -> int:
        if cursor is None:
            return 1
        match = re.fullmatch(r"page:([1-9][0-9]{0,6})", cursor)
        if match is None:
            raise ValueError("invalid SerpAPI page cursor")
        page = int(match.group(1))
        if page <= 1:
            raise ValueError("continuation cursor must target page 2 or later")
        return page

    @staticmethod
    def _next_page_number(value: Any, current_page: int) -> int | None:
        if not value:
            return None
        parsed = urlparse(str(value))
        raw = parse_qs(parsed.query).get("page", [])
        if len(raw) != 1 or not raw[0].isdigit():
            raise ValueError("SerpAPI next pagination URL has no valid page")
        page = int(raw[0])
        if page <= current_page:
            raise ValueError("SerpAPI next page must advance monotonically")
        return page

    @staticmethod
    def _optional_count(value: Any) -> int | None:
        if value is None or value == "":
            return None
        if isinstance(value, bool):
            raise ValueError("SerpAPI count must be an integer")
        normalized = str(value).replace(",", "").strip()
        if not normalized.isdigit():
            raise ValueError("SerpAPI count must be an integer")
        return int(normalized)

    async def fetch(self, request: FetchRequest) -> FetchedDocument:
        publication = (request.publication_number or "").replace(" ", "").upper()
        if not publication:
            match = re.search(r"/patent/([^/]+)", request.url or "", re.IGNORECASE)
            publication = match.group(1).upper() if match else ""
        if not publication:
            raise ValueError("SerpAPI details require a publication number")
        language = (request.language or "en").lower()
        payload = await self._call(
            {
                "engine": self.settings.details_engine,
                "patent_id": f"patent/{publication}/{language}",
            }
        )
        description = ""
        description_link = str(payload.get("description_link") or "")
        if request.include_description and description_link.startswith("https://serpapi.com/"):
            try:
                description = await self.description_transport(description_link)
            except Exception:
                description = ""
        return parse_serpapi_patent_details(
            payload,
            requested_publication=publication,
            language=language,
            description_text=description,
        )

    @staticmethod
    def _extract_window(query_text: str) -> tuple[str, dict[str, str]]:
        window: dict[str, str] = {}
        for match in _WINDOW_PATTERN.finditer(query_text):
            window[match.group(1)] = f"publication:{match.group(2)}"
        return _WINDOW_PATTERN.sub("", query_text).strip(), window

    @staticmethod
    def _extract_assignees(query_text: str) -> tuple[str, list[str]]:
        """Move a pure assignee OR expression to SerpAPI's structured parameter."""
        names = list(dict.fromkeys(_ASSIGNEE_PATTERN.findall(query_text)))
        if not names:
            return query_text, []
        residual = _ASSIGNEE_PATTERN.sub("", query_text)
        residual = re.sub(r"\bOR\b", "", residual, flags=re.IGNORECASE)
        if residual.strip(" ()"):
            return query_text, []
        return names[0], names

    async def _call(self, arguments: dict[str, Any]) -> dict[str, Any]:
        cache_key = "serpapi:" + json.dumps(
            arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        if self.cache is not None:
            try:
                with self.cache.lease(cache_key) as path:
                    return json.loads(path.read_text(encoding="utf-8"))
            except KeyError:
                pass
        key = self.api_key()
        last_error: Exception | None = None
        for attempt in range(1, self.settings.max_attempts + 1):
            try:
                payload = await self.transport({**arguments, "api_key": key})
                if not isinstance(payload, dict):
                    raise SerpApiError(
                        "SERPAPI_CONTRACT_ERROR", "SerpAPI returned a non-object response"
                    )
                if payload.get("error"):
                    error_message = str(payload["error"])
                    if self._is_empty_result_error(error_message):
                        payload = {**payload, "organic_results": []}
                        payload.pop("error", None)
                    else:
                        raise self._payload_error(error_message, key)
                if self.cache is not None:
                    self.cache.put_bytes(
                        cache_key,
                        "searches",
                        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"),
                    )
                return payload
            except SerpApiError as exc:
                last_error = exc
                if not exc.retryable or attempt >= self.settings.max_attempts:
                    raise
            except Exception as exc:
                last_error = SerpApiError(
                    "SERPAPI_NETWORK_ERROR",
                    f"SerpAPI request failed: {type(exc).__name__}",
                    retryable=True,
                )
                if attempt >= self.settings.max_attempts:
                    raise last_error from exc
            await asyncio.sleep(min(2 ** (attempt - 1), 4))
        assert last_error is not None
        raise last_error

    def _payload_error(self, message: str, key: str) -> SerpApiError:
        safe = message.replace(key, "[REDACTED]")[:500]
        lowered = safe.casefold()
        if "api key" in lowered:
            return SerpApiError("SERPAPI_AUTH_ERROR", safe)
        if "run out" in lowered or "rate limit" in lowered or "searches" in lowered:
            return SerpApiError("SERPAPI_RATE_LIMITED", safe)
        return SerpApiError("SERPAPI_ERROR", safe)

    @staticmethod
    def _is_empty_result_error(message: str) -> bool:
        normalized = message.casefold()
        return "hasn't returned any results" in normalized or "has not returned any results" in normalized

    async def _http_json(self, params: dict[str, Any]) -> dict[str, Any]:
        try:
            return await self._http_json_once(params, trust_env=self.settings.trust_environment_proxy)
        except (httpx.ProxyError, httpx.ConnectError, httpx.ConnectTimeout):
            if not self.settings.fallback_to_direct or not self.settings.trust_environment_proxy:
                raise
            return await self._http_json_once(params, trust_env=False)

    async def _http_json_once(
        self, params: dict[str, Any], *, trust_env: bool
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(
            timeout=self.settings.timeout_seconds,
            follow_redirects=True,
            trust_env=trust_env,
        ) as client:
            response = await client.get(str(self.settings.endpoint), params=params)
            try:
                payload = response.json()
            except ValueError as exc:
                raise SerpApiError(
                    "SERPAPI_INVALID_JSON",
                    f"SerpAPI returned invalid JSON with HTTP {response.status_code}",
                    retryable=response.status_code >= 500,
                ) from exc
            if response.status_code == 401:
                raise SerpApiError("SERPAPI_AUTH_ERROR", "SerpAPI rejected the API key")
            if response.status_code == 403:
                raise SerpApiError("SERPAPI_FORBIDDEN", "SerpAPI account is not permitted")
            if response.status_code == 429:
                raise SerpApiError(
                    "SERPAPI_RATE_LIMITED", "SerpAPI quota or hourly rate limit reached"
                )
            if response.status_code >= 500:
                raise SerpApiError(
                    "SERPAPI_SERVER_ERROR",
                    f"SerpAPI server returned HTTP {response.status_code}",
                    retryable=True,
                )
            if response.status_code >= 400:
                raise SerpApiError(
                    "SERPAPI_HTTP_ERROR", f"SerpAPI returned HTTP {response.status_code}"
                )
            return payload

    async def _http_description(self, url: str) -> str:
        async with httpx.AsyncClient(
            timeout=self.settings.timeout_seconds,
            follow_redirects=True,
            trust_env=self.settings.trust_environment_proxy,
        ) as client:
            response = await client.get(url)
            if response.status_code >= 400:
                raise SerpApiError(
                    "SERPAPI_DESCRIPTION_ERROR",
                    f"SerpAPI description returned HTTP {response.status_code}",
                )
            return _description_text(response.text)
