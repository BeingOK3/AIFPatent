from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlparse

import httpx

from ..cache import CacheStore
from ..config import ExaSettings
from .base import FetchRequest, FetchedDocument, SearchHit, SearchProvider, SearchQuery


MCP_PROTOCOL_VERSION = "2025-03-26"
McpTransport = Callable[[dict[str, Any], dict[str, str]], Awaitable[tuple[dict, dict[str, str]]]]
McpCaller = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


class McpProtocolError(RuntimeError):
    pass


class McpHttpClient:
    def __init__(
        self,
        endpoint: str,
        *,
        timeout_seconds: float,
        transport: McpTransport | None = None,
    ):
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds
        self.transport = transport or self._http_transport
        self._request_id = 0

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        initialize_id = self._next_id()
        initialize, response_headers = await self.transport(
            {
                "jsonrpc": "2.0",
                "id": initialize_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "AIFPatent", "version": "1.0"},
                },
            },
            self._headers(),
        )
        self._result(initialize, initialize_id)
        session_id = response_headers.get("mcp-session-id") or response_headers.get(
            "Mcp-Session-Id"
        )
        session_headers = self._headers(session_id)
        await self.transport(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}, session_headers
        )
        call_id = self._next_id()
        response, _ = await self.transport(
            {
                "jsonrpc": "2.0",
                "id": call_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
            session_headers,
        )
        result = self._result(response, call_id)
        if result.get("isError"):
            raise McpProtocolError(_content_text(result) or "MCP tool returned isError")
        return result

    def _headers(self, session_id: str | None = None) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
        }
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        return headers

    async def _http_transport(
        self, payload: dict[str, Any], headers: dict[str, str]
    ) -> tuple[dict, dict[str, str]]:
        try:
            return await self._post(payload, headers, trust_env=True)
        except (ImportError, httpx.ProxyError, httpx.ConnectError):
            return await self._post(payload, headers, trust_env=False)

    async def _post(
        self, payload: dict[str, Any], headers: dict[str, str], *, trust_env: bool
    ) -> tuple[dict, dict[str, str]]:
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            follow_redirects=True,
            trust_env=trust_env,
        ) as client:
            response = await client.post(self.endpoint, json=payload, headers=headers)
            response.raise_for_status()
        return self._decode(response.content, response.headers.get("content-type", "")), dict(
            response.headers
        )

    @staticmethod
    def _decode(content: bytes, content_type: str) -> dict:
        if not content.strip():
            return {}
        text = content.decode("utf-8", errors="replace")
        if "text/event-stream" in content_type or text.lstrip().startswith("event:"):
            messages = []
            for line in text.splitlines():
                if line.startswith("data:"):
                    try:
                        messages.append(json.loads(line[5:].strip()))
                    except json.JSONDecodeError:
                        continue
            if not messages:
                raise McpProtocolError("MCP SSE response has no JSON data event")
            return messages[-1]
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise McpProtocolError("MCP response is not JSON") from exc

    @staticmethod
    def _result(response: dict, expected_id: int) -> dict:
        if response.get("id") != expected_id:
            raise McpProtocolError("MCP response id mismatch")
        if "error" in response:
            error = response["error"]
            raise McpProtocolError(str(error.get("message") if isinstance(error, dict) else error))
        result = response.get("result")
        if not isinstance(result, dict):
            raise McpProtocolError("MCP response has no result object")
        return result

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id


def _content_text(result: dict[str, Any]) -> str:
    parts = []
    for item in result.get("content", []):
        if isinstance(item, dict) and item.get("type") == "text" and item.get("text"):
            parts.append(item["text"])
    return "\n".join(parts)


def _find_results(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        if isinstance(value.get("results"), list):
            return [item for item in value["results"] if isinstance(item, dict)]
        for child in value.values():
            found = _find_results(child)
            if found:
                return found
    elif isinstance(value, list):
        if value and all(isinstance(item, dict) for item in value):
            return value
        for child in value:
            found = _find_results(child)
            if found:
                return found
    return []


def _exa_results(result: dict[str, Any]) -> list[dict[str, Any]]:
    found = _find_results(result.get("structuredContent", {}))
    if found:
        return found
    text = _content_text(result)
    if text:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        found = _find_results(parsed)
        if found:
            return found
        blocks = re.split(r"\n(?=Title:|# )", text)
        fallback = []
        for block in blocks:
            url_match = re.search(r"https?://[^\s)>]+", block)
            if not url_match:
                continue
            title_match = re.search(r"(?:Title:\s*|^#\s*)(.+)", block)
            fallback.append(
                {
                    "title": title_match.group(1).strip() if title_match else "",
                    "url": url_match.group(0).rstrip(".,"),
                    "text": block,
                }
            )
        return fallback
    return []


def _publication_from_url(url: str) -> str | None:
    match = re.search(r"/patent/([^/?#]+)", urlparse(url).path)
    return match.group(1).replace(" ", "").upper() if match else None


def _markdown_section(text: str, heading: str) -> str:
    # EXA occasionally concatenates the first Google heading with the preceding PDF link.
    headings = list(
        re.finditer(r"(?m)(?<!#)#{1,2}\s+([^#\n]+?)\s*(?=\n|$)", text)
    )
    for index, match in enumerate(headings):
        title = match.group(1).strip()
        if title == heading or title.startswith(heading + " ("):
            start = match.end()
            end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
            return text[start:end].strip()
    return ""


def _section_spans(section: str, kind: str) -> list[dict[str, Any]]:
    if not section:
        return []
    if kind == "claims":
        claim_starts = list(re.finditer(r"(?m)^\s*(\d+)\.\s+", section))
        parts = [
            section[
                match.start() : claim_starts[index + 1].start()
                if index + 1 < len(claim_starts)
                else len(section)
            ]
            for index, match in enumerate(claim_starts)
        ] or [section]
    else:
        parts = re.split(r"\n\s*\n", section)
    spans = []
    cursor = 0
    for index, raw in enumerate(parts, start=1):
        part = raw.strip()
        if not part:
            continue
        start = section.find(part, cursor)
        if start < 0:
            continue
        end = start + len(part)
        cursor = end
        if kind == "claims":
            number = re.match(r"(\d+)\.", part)
            label = f"claim {number.group(1)}" if number else f"claim {index}"
        elif kind == "abstract":
            label = "abstract"
        else:
            label = f"paragraph {index}"
        spans.append({"label": label, "start": start, "end": end, "text": part})
    return spans


def parse_exa_patent_markdown(
    text: str, *, provider: str, publication_number: str, url: str, language: str
) -> FetchedDocument:
    """Convert Google Patents markdown returned by EXA into auditable sections."""
    publication = publication_number.replace(" ", "").upper()
    abstract = _markdown_section(text, "Abstract")
    claims = _markdown_section(text, "Claims")
    description = _markdown_section(text, "Description")
    info = _markdown_section(text, "Info")

    def date_after(label: str) -> str | None:
        match = re.search(
            rf"{re.escape(label)}.{{0,700}}?(\d{{4}}-\d{{2}}-\d{{2}})",
            info,
            re.S,
        )
        return match.group(1) if match else None

    title = ""
    for line in text.splitlines():
        if line.startswith("# ") and publication in line.replace(" ", "").upper():
            title = line[2:].strip()
            title = re.sub(r"\s*-\s*Google Patents\s*$", "", title)
            title = re.sub(rf"^{re.escape(publication)}\s*-\s*", "", title, flags=re.I)
            break
    application_match = re.search(
        r"Application number\s*([A-Z]{2}[A-Z0-9./,]+?)\s*(?=Prior art date|Other languages|Other versions|Inventor|Current Assignee|Original Assignee|Priority date|Filing date|Publication date)",
        info,
        re.I,
    )
    assignee_match = re.search(
        r"Current Assignee.*?\)\s*(.+?)\s*Original Assignee", info, re.S
    )
    structured = bool(abstract or claims or description)
    if not structured:
        description = text
    return FetchedDocument(
        provider=provider,
        publication_number=publication,
        application_number=application_match.group(1) if application_match else None,
        title=title,
        assignee=" ".join(assignee_match.group(1).split()) if assignee_match else None,
        priority_date=date_after("Priority date") or date_after("Prior art date"),
        filing_date=date_after("Filing date"),
        publication_date=date_after("Publication date"),
        language=language,
        url=url,
        abstract_text=abstract,
        claims_text=claims,
        description_text=description,
        section_spans={
            "abstract": _section_spans(abstract, "abstract"),
            "claims": _section_spans(claims, "claims"),
            "description": _section_spans(description, "description")
            if structured
            else [
                {"label": "exa fetched content", "start": 0, "end": len(text), "text": text}
            ],
        },
        raw_metadata={"source": "exa_mcp", "structured_sections": structured},
    )


class ExaMcpProvider(SearchProvider):
    name = "exa_mcp"

    def __init__(
        self,
        settings: ExaSettings,
        *,
        cache: CacheStore | None = None,
        caller: McpCaller | None = None,
        transport: McpTransport | None = None,
    ):
        self.settings = settings
        self.cache = cache
        self.client = McpHttpClient(
            str(settings.endpoint), timeout_seconds=settings.timeout_seconds, transport=transport
        )
        self.caller = caller or self.client.call_tool

    async def search(self, query: SearchQuery) -> list[SearchHit]:
        search_text = query.text
        if query.material_types == ["patent"] and "patents.google.com" not in search_text:
            search_text += " site:patents.google.com/patent"
        arguments = {"query": search_text, "numResults": query.limit}
        result = await self._call(self.settings.search_tool, arguments)
        hits = []
        seen = set()
        for item in _exa_results(result):
            url = str(item.get("url") or item.get("id") or "")
            publication = str(item.get("publication_number") or "") or _publication_from_url(url)
            identity = publication or url
            if not identity or identity in seen:
                continue
            seen.add(identity)
            hits.append(
                SearchHit(
                    provider=self.name,
                    provider_rank=len(hits) + 1,
                    title=str(item.get("title") or ""),
                    url=url,
                    publication_number=publication or None,
                    snippet=str(item.get("text") or item.get("snippet") or ""),
                    publication_date=item.get("publishedDate") or item.get("publication_date"),
                    raw=item,
                )
            )
            if len(hits) >= query.limit:
                break
        return hits

    async def fetch(self, request: FetchRequest) -> FetchedDocument:
        publication = (request.publication_number or _publication_from_url(request.url or "") or "").replace(
            " ", ""
        ).upper()
        if not publication:
            raise ValueError("EXA fetch requires a patent publication number")
        url = request.url or f"https://patents.google.com/patent/{publication}/{request.language}"
        result = await self._call(
            self.settings.fetch_tool,
            {"urls": [url], "maxCharacters": self.settings.fetch_max_characters},
        )
        text = _content_text(result)
        if not text:
            records = _exa_results(result)
            text = "\n\n".join(str(item.get("text") or "") for item in records).strip()
        if not text:
            raise ValueError("EXA fetch returned no text content")
        return parse_exa_patent_markdown(
            text,
            provider=self.name,
            publication_number=publication,
            url=url,
            language=request.language,
        )

    async def _call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        cache_key = "exa:mcp:" + json.dumps(
            [tool, arguments], ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        if self.cache is not None:
            try:
                with self.cache.lease(cache_key) as path:
                    return json.loads(path.read_text(encoding="utf-8"))
            except KeyError:
                pass
        last_error: Exception | None = None
        for attempt in range(1, self.settings.max_attempts + 1):
            try:
                result = await self.caller(tool, arguments)
                if not isinstance(result, dict):
                    raise McpProtocolError("MCP caller returned a non-object result")
                if self.cache is not None:
                    self.cache.put_bytes(
                        cache_key,
                        "searches",
                        json.dumps(result, ensure_ascii=False, sort_keys=True).encode("utf-8"),
                    )
                return result
            except Exception as exc:
                last_error = exc
                if attempt < self.settings.max_attempts:
                    await asyncio.sleep(min(2 ** (attempt - 1), 4))
        assert last_error is not None
        raise last_error
