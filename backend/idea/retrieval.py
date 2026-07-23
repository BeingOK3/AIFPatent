from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections import defaultdict
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict

from .agent_schemas import QueryPlannerOutput
from .chunks import has_independent_claim_evidence
from .database import Database, canonical_json, now_ms
from .merge import MergedHit, merge_hits, normalize_publication_number
from .providers import (
    FetchRequest,
    FetchedDocument,
    ProviderResult,
    ProviderRunner,
    ProviderStatus,
    SearchProvider,
    SearchQuery,
)
from .runtime_debug import RunDebugLog
from .search_strategy import (
    DEFAULT_RELEVANCE_THRESHOLD,
    RoundStats,
    SaturationTracker,
    ScreenedCandidate,
    SearchBudget,
    StopReason,
    screen_summaries,
    select_deep_review,
)


class RetrievalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)


class RetrievalResult(RetrievalModel):
    merged_hits: list[MergedHit]
    screened: list[ScreenedCandidate]
    selected_publication_numbers: list[str]
    provider_calls: dict[str, dict[str, int]]
    stop_reason: StopReason
    limitations: list[dict[str, Any]]
    rounds: list[dict[str, Any]]


class FetchResult(RetrievalModel):
    documents: list[FetchedDocument]
    document_ids: dict[str, str]
    limitations: list[dict[str, Any]]


_FETCH_PROVIDER_PRIORITY = {
    "serpapi_google_patents": 0,
    "google_patents_local": 1,
    "exa_mcp": 2,
}


def _fetch_provider_rank(provider: SearchProvider) -> int:
    return _FETCH_PROVIDER_PRIORITY.get(provider.name, 100)


class RetrievalService:
    def __init__(
        self,
        database: Database,
        providers: list[SearchProvider],
        *,
        runner: ProviderRunner | None = None,
        search_timeout_seconds: dict[str, float] | None = None,
        fetch_concurrency: int = 3,
        debug_log: RunDebugLog | None = None,
    ):
        self.database = database
        self.providers = providers
        self.runner = runner or ProviderRunner()
        self.search_timeout_seconds = search_timeout_seconds or {}
        self.fetch_concurrency = fetch_concurrency
        self.debug_log = debug_log

    async def retrieve(
        self,
        *,
        run_id: str,
        plan: QueryPlannerOutput,
        budget: SearchBudget,
        idea_terms: list[str],
        evaluation_date: date,
        saturation_rounds: int,
        saturation_new_high_max: int,
    ) -> RetrievalResult:
        queries_by_round: dict[int, list] = defaultdict(list)
        for query in plan.queries:
            queries_by_round[query.round_number].append(query)
        tracker = SaturationTracker(
            saturation_rounds, saturation_new_high_max, budget.candidate_max
        )
        batches: list[tuple[str, list]] = []
        provider_counts: dict[str, dict[str, int]] = {
            provider.name: defaultdict(int) for provider in self.providers
        }
        limitations: list[dict[str, Any]] = []
        rounds = []
        previous_keys: set[str] = set()
        previous_high_keys: set[str] = set()
        stop_reason: StopReason | None = None
        term_groups = [
            [group.concept, *group.zh_terms, *group.en_terms, *group.broader_terms]
            for group in plan.term_groups
        ]
        screening_terms = [term for group in term_groups for term in group] or idea_terms
        provider_gates = {
            provider.name: asyncio.Semaphore(1) for provider in self.providers
        }
        provider_circuits: dict[str, tuple[str, str] | None] = {
            provider.name: None for provider in self.providers
        }

        async def search_provider(
            provider: SearchProvider, query: SearchQuery
        ) -> ProviderResult:
            async with provider_gates[provider.name]:
                circuit = provider_circuits[provider.name]
                if circuit is not None:
                    return ProviderResult(
                        provider=provider.name,
                        operation="search",
                        request_id=query.query_id,
                        status=ProviderStatus.DISABLED,
                        duration_ms=0,
                        error_code=circuit[0],
                        error_message=circuit[1],
                    )
                result = await self.runner.search(
                    provider,
                    query,
                    timeout_seconds=self.search_timeout_seconds.get(
                        provider.name, 45
                    ),
                )
                if provider.name == "serpapi_google_patents" and result.error_code in {
                    "SERPAPI_API_KEY_REQUIRED",
                    "SERPAPI_CREDENTIAL_FILE_INVALID",
                    "SERPAPI_AUTH_ERROR",
                    "SERPAPI_FORBIDDEN",
                    "SERPAPI_RATE_LIMITED",
                }:
                    provider_circuits[provider.name] = (
                        result.error_code,
                        result.error_message or "SerpAPI disabled for this run",
                    )
                elif (
                    provider.name == "exa_mcp"
                    and result.error_message
                    and "429 Too Many Requests" in result.error_message
                ):
                    provider_circuits[provider.name] = (
                        "EXA_RATE_LIMITED",
                        "Exa quota or rate limit reached; remaining queries skipped",
                    )
                return result

        for round_number in sorted(queries_by_round):
            call_specs = []
            for planned in queries_by_round[round_number]:
                query_id = f"{run_id}:{planned.query_id}"
                query = SearchQuery(
                    query_id=query_id,
                    text=planned.query_text,
                    language=planned.language,
                    round_number=round_number,
                    limit=budget.per_query_limit,
                    query_type=planned.query_type,
                )
                for provider in self.providers:
                    call_specs.append((provider, query))
            if self.debug_log:
                for provider, query in call_specs:
                    self.debug_log.append(
                        run_id,
                        "tool_call_started",
                        step_name="RETRIEVE_CANDIDATES",
                        provider=provider.name,
                        operation="search",
                        query_id=query.query_id,
                        round_number=query.round_number,
                    )
            results = await asyncio.gather(
                *[
                    search_provider(provider, query)
                    for provider, query in call_specs
                ]
            )
            successful_providers = set()
            for (provider, query), result in zip(call_specs, results, strict=True):
                self._record_provider_result(run_id, "RETRIEVE_CANDIDATES", result)
                provider_counts[provider.name][result.status.value] += 1
                if result.succeeded:
                    successful_providers.add(provider.name)
                    batches.append((query.query_id, result.hits))
                    self._record_search_hits(run_id, query.query_id, result.hits)

            merged = merge_hits(batches)[: budget.candidate_max]
            screened = screen_summaries(
                merged,
                idea_terms=screening_terms,
                evaluation_date=evaluation_date,
                term_groups=term_groups,
            )
            current_keys = {hit.merge_key for hit in merged}
            high_keys = {
                item.hit.merge_key
                for item in screened
                if item.relevance_score >= 0.5
                and item.date_status != "AFTER_EVALUATION_DATE"
            }
            stats = RoundStats(
                round_number=round_number,
                total_candidates=len(merged),
                new_families=len(current_keys - previous_keys),
                new_high_relevance_families=len(high_keys - previous_high_keys),
                successful_providers=len(successful_providers),
            )
            stop_reason = tracker.add(stats)
            rounds.append(
                {
                    **stats.__dict__,
                    "provider_statuses": {
                        name: dict(counts) for name, counts in provider_counts.items()
                    },
                }
            )
            previous_keys = current_keys
            previous_high_keys = high_keys
            if stop_reason is not None:
                break

        merged = merge_hits(batches)[: budget.candidate_max]
        screened = screen_summaries(
            merged,
            idea_terms=screening_terms,
            evaluation_date=evaluation_date,
            term_groups=term_groups,
        )
        selection = select_deep_review(screened, budget)
        unidentifiable_count = sum(
            1
            for item in screened
            if item.date_status != "AFTER_EVALUATION_DATE"
            and item.relevance_score >= DEFAULT_RELEVANCE_THRESHOLD
            and normalize_publication_number(item.hit.publication_number) is None
        )
        if unidentifiable_count:
            limitations.append(
                {
                    "code": "DEEP_REVIEW_IDENTIFIER_MISSING",
                    "count": unidentifiable_count,
                    "message": "部分相关候选缺少可核验的专利公开号，已排除在全文深读之外。",
                }
            )
        if selection.limitation:
            limitations.append(selection.limitation)
        for provider, counts in provider_counts.items():
            successes = counts.get(ProviderStatus.SUCCESS.value, 0) + counts.get(
                ProviderStatus.EMPTY.value, 0
            )
            failures = sum(counts.values()) - successes
            if failures and successes == 0:
                limitations.append(
                    {
                        "code": "PROVIDER_DEGRADED",
                        "provider": provider,
                        "statuses": dict(counts),
                    }
                )
        if stop_reason is None:
            stop_reason = StopReason.QUERY_EXHAUSTED
        return RetrievalResult(
            merged_hits=merged,
            screened=screened,
            selected_publication_numbers=list(
                dict.fromkeys(
                    publication
                    for item in selection.selected
                    if (
                        publication := normalize_publication_number(
                            item.hit.publication_number
                        )
                    )
                )
            ),
            provider_calls={name: dict(counts) for name, counts in provider_counts.items()},
            stop_reason=stop_reason,
            limitations=limitations,
            rounds=rounds,
        )

    async def fetch_selected(
        self,
        *,
        run_id: str,
        retrieval: RetrievalResult,
        language: str = "en",
        minimum_documents: int = 10,
    ) -> FetchResult:
        by_publication = {}
        for hit in retrieval.merged_hits:
            publication = normalize_publication_number(hit.publication_number)
            if publication and publication not in by_publication:
                by_publication[publication] = hit

        selected_publications = []
        invalid_count = 0
        duplicate_count = 0
        missing_publications = []
        seen_publications = set()
        for raw_publication in retrieval.selected_publication_numbers:
            publication = normalize_publication_number(raw_publication)
            if publication is None:
                invalid_count += 1
                continue
            if publication in seen_publications:
                duplicate_count += 1
                continue
            seen_publications.add(publication)
            if publication not in by_publication:
                missing_publications.append(publication)
                continue
            selected_publications.append(publication)

        reserve_publications = []
        reserved = set(selected_publications)
        for candidate in retrieval.screened:
            publication = normalize_publication_number(candidate.hit.publication_number)
            if (
                publication
                and publication in by_publication
                and publication not in reserved
                and candidate.date_status != "AFTER_EVALUATION_DATE"
                and candidate.relevance_score >= DEFAULT_RELEVANCE_THRESHOLD
            ):
                reserve_publications.append(publication)
                reserved.add(publication)

        limitations = []
        if invalid_count:
            limitations.append(
                {
                    "code": "DEEP_REVIEW_IDENTIFIER_MISSING",
                    "count": invalid_count,
                    "message": "部分深读候选缺少有效专利公开号，已安全跳过。",
                }
            )
        if duplicate_count:
            limitations.append(
                {
                    "code": "DUPLICATE_DEEP_REVIEW_SELECTION",
                    "count": duplicate_count,
                    "message": "重复的深读公开号已合并，仅抓取一次。",
                }
            )
        if missing_publications:
            limitations.append(
                {
                    "code": "DEEP_REVIEW_CANDIDATE_NOT_FOUND",
                    "publication_numbers": missing_publications,
                    "message": "部分深读公开号无法在本次候选集中定位，已安全跳过。",
                }
            )
        semaphore = asyncio.Semaphore(self.fetch_concurrency)
        provider_priority = sorted(
            self.providers,
            key=lambda provider: (
                0
                if retrieval.provider_calls.get(provider.name, {}).get(
                    ProviderStatus.SUCCESS.value, 0
                )
                or retrieval.provider_calls.get(provider.name, {}).get(
                    ProviderStatus.EMPTY.value, 0
                )
                else 1,
                _fetch_provider_rank(provider),
            ),
        )

        async def fetch_one(publication: str):
            async with semaphore:
                hit = by_publication[publication]
                return await self._fetch_with_fallback(
                    run_id, publication, hit.urls, language, providers=provider_priority
                )

        tasks = [
            asyncio.create_task(fetch_one(publication))
            for publication in selected_publications
        ]
        try:
            outcomes = await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        documents = []
        document_ids = {}
        for publication, (document, failures) in zip(
            selected_publications, outcomes, strict=True
        ):
            if document is None:
                limitations.append(
                    {
                        "code": "DOCUMENT_FETCH_FAILED",
                        "publication_number": publication,
                        "providers": failures,
                    }
                )
                continue
            document_id = self._persist_document(run_id, document, by_publication[publication])
            documents.append(document)
            document_ids[publication] = document_id
        backfilled = []
        target_count = len(selected_publications)
        for publication in reserve_publications:
            if len(documents) >= target_count:
                break
            document, failures = await fetch_one(publication)
            if document is None:
                limitations.append(
                    {
                        "code": "DOCUMENT_FETCH_FAILED",
                        "publication_number": publication,
                        "providers": failures,
                    }
                )
                continue
            document_id = self._persist_document(
                run_id, document, by_publication[publication]
            )
            documents.append(document)
            document_ids[publication] = document_id
            backfilled.append(publication)
        if backfilled:
            limitations.append(
                {
                    "code": "DEEP_REVIEW_BACKFILLED",
                    "count": len(backfilled),
                    "publication_numbers": backfilled,
                    "message": "部分首选专利全文证据不完整，已使用同轮合格候补补位。",
                }
            )
        if len(documents) < minimum_documents:
            limitations.append(
                {
                    "code": "DEEP_REVIEW_FETCHED_BELOW_MINIMUM",
                    "required": minimum_documents,
                    "fetched": len(documents),
                    "message": (
                        f"仅成功获取 {len(documents)} 篇可深度核验全文，"
                        f"低于配置下限 {minimum_documents} 篇；后续结论将明确降级。"
                    ),
                }
            )
        return FetchResult(
            documents=documents, document_ids=document_ids, limitations=limitations
        )

    async def rehydrate_document(
        self,
        *,
        run_id: str,
        publication_number: str,
        url: str = "",
        language: str = "en",
    ) -> tuple[FetchedDocument | None, tuple[str, ...]]:
        """Fetch one historical document through the normal provider controls.

        Rehydration deliberately does not update SQLite's transient document store;
        the corpus migrator validates identity and owns the durable write.
        """
        document, failures = await self._fetch_with_fallback(
            run_id,
            publication_number,
            [url] if url else [],
            language,
        )
        return document, tuple(
            f"{failure['provider']}:{failure['status']}" for failure in failures
        )

    async def _fetch_with_fallback(
        self,
        run_id: str,
        publication: str,
        urls: list[str],
        language: str,
        *,
        providers: list[SearchProvider] | None = None,
    ) -> tuple[FetchedDocument | None, list[dict[str, str]]]:
        ordered = providers or sorted(
            self.providers,
            key=_fetch_provider_rank,
        )
        failures = []
        for provider in ordered:
            selected_url = next((url for url in urls if "/patent/" in url), None)
            url_language = (
                selected_url.rstrip("/").rsplit("/", 1)[-1]
                if selected_url
                else language
            )
            request = FetchRequest(
                request_id=f"{run_id}:FETCH:{publication}:{provider.name}",
                publication_number=publication,
                url=selected_url,
                language=url_language if url_language in {"zh", "en"} else language,
            )
            if self.debug_log:
                self.debug_log.append(
                    run_id,
                    "tool_call_started",
                    step_name="NORMALIZE_AND_FETCH",
                    provider=provider.name,
                    operation="fetch",
                    publication_number=publication,
                    request_id=request.request_id,
                )
            result = await self.runner.fetch(
                provider,
                request,
                timeout_seconds=self.search_timeout_seconds.get(provider.name, 45),
            )
            if result.status == ProviderStatus.SUCCESS and result.document is not None:
                evidence_error = self._required_evidence_error(result.document)
                if evidence_error is not None:
                    code, message = evidence_error
                    result = result.model_copy(
                        update={
                            "status": ProviderStatus.CONTRACT_ERROR,
                            "document": None,
                            "error_code": code,
                            "error_message": message,
                        }
                    )
            self._record_provider_result(run_id, "NORMALIZE_AND_FETCH", result)
            if result.status == ProviderStatus.SUCCESS and result.document is not None:
                return result.document, failures
            failures.append(
                {
                    "provider": provider.name,
                    "status": result.status.value,
                    "error_code": result.error_code or "",
                }
            )
        return None, failures

    @staticmethod
    def _required_evidence_error(
        document: FetchedDocument,
    ) -> tuple[str, str] | None:
        if not document.abstract_text.strip():
            return (
                "ABSTRACT_MISSING",
                "Patent detail response has no abstract required by IDEA report evidence",
            )
        if not has_independent_claim_evidence(document):
            return (
                "INDEPENDENT_CLAIM_MISSING",
                "Patent detail response has no recognizable independent claim",
            )
        return None

    def _record_provider_result(
        self, run_id: str, step_name: str, result: ProviderResult
    ) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO tool_calls(
                    call_id,run_id,step_name,provider,operation,request_json,
                    response_summary_json,result_count,duration_ms,status,
                    error_code,error_message,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    str(uuid.uuid4()),
                    run_id,
                    step_name,
                    result.provider,
                    result.operation,
                    canonical_json({"request_id": result.request_id}),
                    canonical_json(
                        {
                            "status": result.status.value,
                            "has_document": result.document is not None,
                        }
                    ),
                    len(result.hits) if result.operation == "search" else int(result.document is not None),
                    result.duration_ms,
                    result.status.value,
                    result.error_code,
                    result.error_message,
                    now_ms(),
                ),
            )
        if self.debug_log:
            self.debug_log.append(
                run_id,
                "tool_call_completed",
                step_name=step_name,
                provider=result.provider,
                operation=result.operation,
                status=result.status.value,
                result_count=(
                    len(result.hits)
                    if result.operation == "search"
                    else int(result.document is not None)
                ),
                duration_ms=result.duration_ms,
                error_code=result.error_code,
                error_message=result.error_message,
            )

    def _record_search_hits(self, run_id: str, query_id: str, hits: list) -> None:
        with self.database.connect() as connection:
            timestamp = now_ms()
            for hit in hits:
                connection.execute(
                    """
                    INSERT INTO search_hits(
                        hit_id,run_id,query_id,provider,provider_rank,title,url,
                        publication_number,application_number,family_id,snippet,
                        raw_json,normalized_key,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        str(uuid.uuid4()),
                        run_id,
                        query_id,
                        hit.provider,
                        hit.provider_rank,
                        hit.title,
                        hit.url,
                        normalize_publication_number(hit.publication_number),
                        hit.application_number,
                        hit.family_id,
                        hit.snippet,
                        canonical_json(hit.raw),
                        normalize_publication_number(hit.publication_number) or hit.url,
                        timestamp,
                    ),
                )

    def _persist_document(
        self, run_id: str, document: FetchedDocument, merged_hit: MergedHit
    ) -> str:
        content = "\n\n".join(
            [document.abstract_text, document.claims_text, document.description_text]
        )
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        timestamp = now_ms()
        with self.database.connect() as connection:
            existing = connection.execute(
                """
                SELECT document_id FROM patent_documents
                WHERE publication_number = ? AND language = ?
                """,
                (document.publication_number, document.language),
            ).fetchone()
            if existing:
                document_id = existing["document_id"]
                if document.family_id:
                    connection.execute(
                        "INSERT OR IGNORE INTO patent_families(family_id,source) VALUES(?,?)",
                        (document.family_id, document.provider),
                    )
                connection.execute(
                    """
                    UPDATE patent_documents SET
                        application_number = COALESCE(?,application_number),
                        family_id = COALESCE(?,family_id), title = COALESCE(NULLIF(?,''),title),
                        assignee = COALESCE(?,assignee), inventors_json = ?,
                        priority_date = COALESCE(?,priority_date), filing_date = COALESCE(?,filing_date),
                        publication_date = COALESCE(?,publication_date), grant_date = COALESCE(?,grant_date),
                        url = COALESCE(NULLIF(?,''),url), abstract_text = ?, claims_text = ?,
                        description_text = ?, content_hash = ?, metadata_json = ?, updated_at = ?
                    WHERE document_id = ?
                    """,
                    (
                        document.application_number, document.family_id,
                        document.title or merged_hit.title, document.assignee or merged_hit.assignee,
                        canonical_json(document.inventors),
                        document.priority_date or merged_hit.priority_date,
                        document.filing_date or merged_hit.filing_date,
                        document.publication_date or merged_hit.publication_date,
                        document.grant_date, document.url, document.abstract_text,
                        document.claims_text, document.description_text, content_hash,
                        canonical_json({**document.raw_metadata, "section_spans": document.section_spans}),
                        timestamp, document_id,
                    ),
                )
            else:
                document_id = str(uuid.uuid4())
                if document.family_id:
                    connection.execute(
                        "INSERT OR IGNORE INTO patent_families(family_id,source) VALUES(?,?)",
                        (document.family_id, document.provider),
                    )
                connection.execute(
                    """
                    INSERT INTO patent_documents(
                        document_id,publication_number,application_number,family_id,title,
                        assignee,inventors_json,priority_date,filing_date,publication_date,
                        grant_date,language,url,abstract_text,claims_text,description_text,
                        content_hash,metadata_json,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        document_id,
                        document.publication_number,
                        document.application_number,
                        document.family_id,
                        document.title or merged_hit.title,
                        document.assignee or merged_hit.assignee,
                        canonical_json(document.inventors),
                        document.priority_date or merged_hit.priority_date,
                        document.filing_date or merged_hit.filing_date,
                        document.publication_date or merged_hit.publication_date,
                        document.grant_date,
                        document.language,
                        document.url,
                        document.abstract_text,
                        document.claims_text,
                        document.description_text,
                        content_hash,
                        canonical_json(
                            {
                                **document.raw_metadata,
                                "section_spans": document.section_spans,
                            }
                        ),
                        timestamp,
                        timestamp,
                    ),
                )
            connection.execute(
                """
                INSERT OR REPLACE INTO run_documents(
                    run_id,document_id,relevance,relevance_score,screening_status,
                    deep_reviewed,found_by_json,query_ids_json
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    run_id,
                    document_id,
                    None,
                    None,
                    "FETCHED",
                    0,
                    canonical_json(merged_hit.found_by),
                    canonical_json(merged_hit.query_ids),
                ),
            )
        return document_id
