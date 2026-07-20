from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from collections.abc import Awaitable, Callable
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin, urlparse

import httpx

from ..cache import CacheStore
from ..config import GooglePatentsSettings
from .base import FetchRequest, FetchedDocument, SearchHit, SearchProvider, SearchQuery


HttpGetter = Callable[[str], Awaitable[bytes]]


def _classes(attrs: dict[str, str | None]) -> set[str]:
    return set((attrs.get("class") or "").split())


class _GoogleSearchParser(HTMLParser):
    VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.records: list[dict[str, str]] = []
        self.record: dict[str, str] | None = None
        self.depth = 0
        self.capture: str | None = None
        self.capture_tag: str | None = None

    def handle_starttag(self, tag: str, attrs_list):
        attrs = dict(attrs_list)
        classes = _classes(attrs)
        if self.record is None and (
            (tag == "article" and "result" in classes) or tag == "search-result-item"
        ):
            self.record = {}
            self.depth = 1
        elif self.record is not None and tag not in self.VOID_TAGS:
            self.depth += 1

        if self.record is None:
            return
        if tag == "a" and "/patent/" in (attrs.get("href") or ""):
            href = attrs.get("href") or ""
            self.record.setdefault("url", urljoin(self.base_url, href))
            publication = _publication_from_url(href)
            if publication:
                self.record.setdefault("publication_number", publication)
        field = self._field(tag, classes)
        if field:
            if field in {
                "publication_number",
                "application_number",
                "priority_date",
                "filing_date",
                "publication_date",
                "assignee",
            }:
                self.record[field] = ""
            self.capture = field
            self.capture_tag = tag

    def handle_endtag(self, tag: str):
        if self.record is None:
            return
        if self.capture_tag == tag:
            self.capture = None
            self.capture_tag = None
        self.depth -= 1
        if self.depth == 0:
            if self.record.get("url") or self.record.get("publication_number"):
                self.records.append(self.record)
            self.record = None

    def handle_data(self, data: str):
        if self.record is None or self.capture is None:
            return
        text = " ".join(data.split())
        if not text:
            return
        previous = self.record.get(self.capture, "")
        self.record[self.capture] = f"{previous} {text}".strip()

    @staticmethod
    def _field(tag: str, classes: set[str]) -> str | None:
        if tag == "h3" or "title" in classes:
            return "title"
        mapping = {
            "publication-number": "publication_number",
            "application-number": "application_number",
            "priority-date": "priority_date",
            "filing-date": "filing_date",
            "publication-date": "publication_date",
            "assignee": "assignee",
            "abstract": "snippet",
            "snippet": "snippet",
        }
        for class_name, field in mapping.items():
            if class_name in classes:
                return field
        return None


def _publication_from_url(url: str) -> str | None:
    parts = urlparse(url).path.split("/")
    try:
        value = parts[parts.index("patent") + 1]
    except (ValueError, IndexError):
        return None
    return value.replace(" ", "").upper() or None


def parse_search_html(content: str, base_url: str) -> list[dict[str, str]]:
    parser = _GoogleSearchParser(base_url)
    parser.feed(content)
    return parser.records


def _plain_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", value)).split())


def parse_search_json(content: str, base_url: str) -> list[dict[str, str]]:
    """Parse the JSON response used by the current Google Patents search UI."""
    payload = json.loads(content)
    results = payload.get("results")
    if not isinstance(results, dict):
        raise ValueError("Google Patents search response has no results object")
    clusters = results.get("cluster", [])
    if not isinstance(clusters, list):
        raise ValueError("Google Patents search response has invalid clusters")

    records: list[dict[str, str]] = []
    for cluster in clusters:
        if not isinstance(cluster, dict):
            continue
        cluster_results = cluster.get("result", [])
        if not isinstance(cluster_results, list):
            continue
        for item in cluster_results:
            if not isinstance(item, dict):
                continue
            patent = item.get("patent")
            if not isinstance(patent, dict):
                continue
            identifier = item.get("id")
            publication = _plain_text(patent.get("publication_number")).replace(" ", "").upper()
            language = _plain_text(patent.get("language")) or "en"
            url = ""
            if isinstance(identifier, str) and identifier:
                url = urljoin(base_url.rstrip("/") + "/", identifier.lstrip("/"))
            elif publication:
                url = urljoin(base_url.rstrip("/") + "/", f"patent/{publication}/{language}")
            records.append(
                {
                    "url": url,
                    "publication_number": publication,
                    "title": _plain_text(patent.get("title")),
                    "snippet": _plain_text(patent.get("snippet")),
                    "priority_date": _plain_text(patent.get("priority_date")),
                    "filing_date": _plain_text(patent.get("filing_date")),
                    "publication_date": _plain_text(patent.get("publication_date")),
                    "assignee": _plain_text(patent.get("assignee")),
                }
            )
    return records


@dataclass
class _Capture:
    kind: str
    depth: int
    parts: list[str] = field(default_factory=list)


class _GooglePatentParser(HTMLParser):
    VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
    SCALAR_PROPS = {
        "publicationNumber": "publication_number",
        "applicationNumber": "application_number",
        "title": "title",
        "assigneeOriginal": "assignee",
        "priorityDate": "priority_date",
        "filingDate": "filing_date",
        "publicationDate": "publication_date",
        "grantDate": "grant_date",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.captures: list[_Capture] = []
        self.values: dict[str, str] = {}
        self.inventors: list[str] = []
        self.claims: list[str] = []
        self.description_lines: list[str] = []
        self.fallback_claims = ""
        self.fallback_description = ""

    def handle_starttag(self, tag: str, attrs_list):
        is_void = tag in self.VOID_TAGS
        if not is_void:
            self.depth += 1
        attrs = dict(attrs_list)
        classes = _classes(attrs)
        itemprop = attrs.get("itemprop") or ""

        if tag == "meta":
            self._meta(attrs)
        if itemprop in self.SCALAR_PROPS:
            datetime_value = attrs.get("datetime")
            if datetime_value:
                self.values.setdefault(self.SCALAR_PROPS[itemprop], datetime_value)
            else:
                self.captures.append(_Capture(self.SCALAR_PROPS[itemprop], self.depth))
        elif itemprop == "inventor":
            self.captures.append(_Capture("inventor", self.depth))
        elif itemprop == "abstract":
            self.captures.append(_Capture("abstract", self.depth))
        elif itemprop == "claims":
            self.captures.append(_Capture("claims_section", self.depth))
        elif itemprop == "description":
            self.captures.append(_Capture("description_section", self.depth))

        if "claim" in classes and "claim-text" not in classes:
            self.captures.append(_Capture("claim", self.depth))
        if "description-line" in classes:
            self.captures.append(_Capture("description_line", self.depth))

    def handle_startendtag(self, tag: str, attrs_list):
        self.handle_starttag(tag, attrs_list)

    def handle_endtag(self, tag: str):
        completed = [capture for capture in self.captures if capture.depth == self.depth]
        for capture in completed:
            self.captures.remove(capture)
            self._finish_capture(capture)
        self.depth = max(0, self.depth - 1)

    def handle_data(self, data: str):
        text = " ".join(data.split())
        if not text:
            return
        for capture in self.captures:
            capture.parts.append(text)

    def _meta(self, attrs: dict[str, str | None]) -> None:
        name = attrs.get("name") or ""
        scheme = (attrs.get("scheme") or "").lower()
        content = (attrs.get("content") or "").strip()
        if not content:
            return
        if name == "DC.title":
            self.values.setdefault("title", content)
        elif name == "DC.date":
            self.values.setdefault("publication_date", content)
        elif name == "DC.contributor" and scheme == "inventor":
            if content not in self.inventors:
                self.inventors.append(content)
        elif name == "DC.contributor" and scheme in {"assignee", "assigneeoriginal"}:
            self.values.setdefault("assignee", content)

    def _finish_capture(self, capture: _Capture) -> None:
        text = " ".join(capture.parts).strip()
        if not text:
            return
        if capture.kind == "inventor":
            if text not in self.inventors:
                self.inventors.append(text)
        elif capture.kind == "claim":
            if text not in self.claims:
                self.claims.append(text)
        elif capture.kind == "description_line":
            if text not in self.description_lines:
                self.description_lines.append(text)
        elif capture.kind == "claims_section":
            if len(text) > len(self.fallback_claims):
                self.fallback_claims = text
        elif capture.kind == "description_section":
            if len(text) > len(self.fallback_description):
                self.fallback_description = text
        elif capture.kind == "abstract":
            if len(text) > len(self.values.get("abstract", "")):
                self.values["abstract"] = text
        else:
            self.values.setdefault(capture.kind, text)


def _join_spans(parts: list[str], kind: str) -> tuple[str, list[dict]]:
    content = ""
    spans: list[dict] = []
    for index, part in enumerate(parts, start=1):
        if content:
            content += "\n\n"
        start = len(content)
        content += part
        end = len(content)
        if kind == "claim":
            match = re.match(r"\s*(\d+)\s*[.)]?", part)
            label = f"claim {match.group(1)}" if match else f"claim {index}"
        else:
            match = re.match(r"\s*\[?(\d{4})\]?", part)
            label = f"[{match.group(1)}]" if match else f"paragraph {index}"
        spans.append({"label": label, "start": start, "end": end, "text": part})
    return content, spans


def parse_patent_html(content: str, *, provider: str, url: str, language: str) -> FetchedDocument:
    parser = _GooglePatentParser()
    parser.feed(content)
    publication = (parser.values.get("publication_number") or "").replace(" ", "").upper()
    if not publication:
        raise ValueError("Google Patents page has no publication number")
    claims_parts = parser.claims or ([parser.fallback_claims] if parser.fallback_claims else [])
    description_parts = parser.description_lines or (
        [parser.fallback_description] if parser.fallback_description else []
    )
    claims_text, claim_spans = _join_spans(claims_parts, "claim")
    description_text, description_spans = _join_spans(description_parts, "description")
    abstract = parser.values.get("abstract", "")
    if not abstract and not claims_text and not description_text:
        raise ValueError("Google Patents page has no patent text sections")
    abstract_spans = (
        [{"label": "abstract", "start": 0, "end": len(abstract), "text": abstract}]
        if abstract
        else []
    )
    return FetchedDocument(
        provider=provider,
        publication_number=publication,
        application_number=parser.values.get("application_number"),
        title=parser.values.get("title", ""),
        assignee=parser.values.get("assignee"),
        inventors=parser.inventors,
        priority_date=parser.values.get("priority_date"),
        filing_date=parser.values.get("filing_date"),
        publication_date=parser.values.get("publication_date"),
        grant_date=parser.values.get("grant_date"),
        language=language,
        url=url,
        abstract_text=abstract,
        claims_text=claims_text,
        description_text=description_text,
        section_spans={
            "abstract": abstract_spans,
            "claims": claim_spans,
            "description": description_spans,
        },
        raw_metadata={"parser": "google_patents_html_v1"},
    )


class GooglePatentsProvider(SearchProvider):
    name = "google_patents_local"

    def __init__(
        self,
        settings: GooglePatentsSettings,
        *,
        cache: CacheStore | None = None,
        http_getter: HttpGetter | None = None,
    ):
        self.settings = settings
        self.cache = cache
        self.http_getter = http_getter
        self._rate_lock = asyncio.Lock()
        self._last_request_at = 0.0

    def build_search_url(self, query: SearchQuery) -> str:
        parameters = {
            "q": query.text,
            "num": query.limit,
            "page": max(0, query.round_number - 1),
        }
        if query.countries:
            parameters["country"] = ",".join(query.countries)
        nested_query = urlencode(parameters)
        return (
            str(self.settings.base_url).rstrip("/")
            + "/xhr/query?"
            + urlencode({"url": nested_query})
        )

    async def search(self, query: SearchQuery) -> list[SearchHit]:
        url = self.build_search_url(query)
        content = await self._get(url, category="searches")
        records = parse_search_json(
            content.decode("utf-8", errors="replace"), str(self.settings.base_url)
        )
        hits: list[SearchHit] = []
        seen: set[str] = set()
        for record in records:
            publication = (record.get("publication_number") or "").replace(" ", "").upper()
            identity = publication or record.get("url", "")
            if not identity or identity in seen:
                continue
            seen.add(identity)
            hits.append(
                SearchHit(
                    provider=self.name,
                    provider_rank=len(hits) + 1,
                    title=record.get("title", ""),
                    url=record.get("url", ""),
                    publication_number=publication or None,
                    application_number=record.get("application_number"),
                    snippet=record.get("snippet", ""),
                    priority_date=record.get("priority_date"),
                    filing_date=record.get("filing_date"),
                    publication_date=record.get("publication_date"),
                    assignee=record.get("assignee"),
                    raw=record,
                )
            )
            if len(hits) >= query.limit:
                break
        return hits

    async def fetch(self, request: FetchRequest) -> FetchedDocument:
        if request.url:
            url = request.url
        else:
            publication = (request.publication_number or "").replace(" ", "").upper()
            url = (
                str(self.settings.base_url).rstrip("/")
                + f"/patent/{publication}/{request.language}"
            )
        content = await self._get(url, category="documents")
        return parse_patent_html(
            content.decode("utf-8", errors="replace"),
            provider=self.name,
            url=url,
            language=request.language,
        )

    async def _get(self, url: str, *, category: str) -> bytes:
        cache_key = f"gpat:http:{url}"
        if self.cache is not None:
            try:
                with self.cache.lease(cache_key) as path:
                    return path.read_bytes()
            except KeyError:
                pass

        last_error: Exception | None = None
        for attempt in range(1, self.settings.max_attempts + 1):
            try:
                await self._wait_for_rate_limit()
                if self.http_getter is not None:
                    content = await self.http_getter(url)
                else:
                    content = await self._http_get_with_proxy_fallback(url)
                if self.cache is not None:
                    self.cache.put_bytes(cache_key, category, content)
                return content
            except Exception as exc:
                last_error = exc
                if attempt < self.settings.max_attempts:
                    await asyncio.sleep(min(2 ** (attempt - 1), 4))
        assert last_error is not None
        raise last_error

    async def _http_get_with_proxy_fallback(self, url: str) -> bytes:
        try:
            return await self._http_get(url, trust_env=self.settings.trust_environment_proxy)
        except (ImportError, httpx.ProxyError, httpx.ConnectError):
            if not self.settings.fallback_to_direct or not self.settings.trust_environment_proxy:
                raise
            return await self._http_get(url, trust_env=False)

    async def _http_get(self, url: str, *, trust_env: bool) -> bytes:
        async with httpx.AsyncClient(
            timeout=self.settings.timeout_seconds,
            headers={"User-Agent": self.settings.user_agent},
            follow_redirects=True,
            trust_env=trust_env,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.content

    async def _wait_for_rate_limit(self) -> None:
        async with self._rate_lock:
            now = time.monotonic()
            wait = self.settings.min_request_interval_seconds - (now - self._last_request_at)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request_at = time.monotonic()
