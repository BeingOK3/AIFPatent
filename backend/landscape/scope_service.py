from __future__ import annotations

import asyncio
import uuid
from datetime import date
from typing import Protocol

from .scope import (
    CompanyScopeDraft,
    ScopeDraft,
    ScopeDraftLimitation,
    ScopeDraftStatus,
    TechnologyTermCandidate,
    make_scope_draft_id,
    normalize_scope_text,
)
from .scope_expansion import (
    ScopeExpansionService,
    fallback_company_scope,
    fallback_technology_terms,
)
from .scope_repository import CompanyProfileMemory


class ScopePreparationError(ValueError):
    pass


class ScopeDraftRepositoryPort(Protocol):
    def find_company_memory(self, input_name: str) -> CompanyProfileMemory | None: ...
    def create(self, scope: ScopeDraft) -> ScopeDraft: ...
    def get(self, draft_id: str) -> ScopeDraft: ...
    def update(self, scope: ScopeDraft, *, expected_revision: int) -> ScopeDraft: ...


class ScopeExpansionPort(Protocol):
    async def expand_company(
        self,
        input_name: str,
        *,
        memory: CompanyProfileMemory | None = None,
    ) -> CompanyScopeDraft: ...

    async def expand_technology(
        self,
        original_input: str,
    ) -> tuple[TechnologyTermCandidate, ...]: ...


class ScopeDraftPreparationService:
    """Persist a reviewable draft while expanding independent inputs concurrently."""

    def __init__(
        self,
        repository: ScopeDraftRepositoryPort,
        expansion: ScopeExpansionPort,
        *,
        max_company_concurrency: int = 4,
        object_timeout_seconds: float = 75,
    ):
        if max_company_concurrency < 1 or max_company_concurrency > 16:
            raise ValueError("max_company_concurrency must be between 1 and 16")
        if object_timeout_seconds <= 0 or object_timeout_seconds > 300:
            raise ValueError("object_timeout_seconds must be between 0 and 300")
        self.repository = repository
        self.expansion = expansion
        self.max_company_concurrency = max_company_concurrency
        self.object_timeout_seconds = object_timeout_seconds

    async def prepare(
        self,
        *,
        company_names: tuple[str, ...] = (),
        technology_input: str | None = None,
        publication_start: date,
        publication_end: date,
        draft_id: str | None = None,
    ) -> ScopeDraft:
        initial = self.create_draft(
            company_names=company_names,
            technology_input=technology_input,
            publication_start=publication_start,
            publication_end=publication_end,
            draft_id=draft_id,
        )
        return await self.expand_draft(
            initial.draft_id,
            expected_revision=initial.revision,
        )

    def create_draft(
        self,
        *,
        company_names: tuple[str, ...] = (),
        technology_input: str | None = None,
        publication_start: date,
        publication_end: date,
        draft_id: str | None = None,
    ) -> ScopeDraft:
        companies = _validate_company_inputs(company_names)
        technology = technology_input.strip() if technology_input else None
        if not companies and not technology:
            raise ScopePreparationError(
                "at least one company or a technology direction is required"
            )
        if publication_end < publication_start:
            raise ScopePreparationError(
                "publication_end must be on or after publication_start"
            )

        # These are short PostgreSQL checkpoints. Keeping them on the request
        # thread avoids leaking default-executor threads during worker shutdown;
        # the high-latency remote model work below remains asynchronously parallel.
        memories = tuple(
            self.repository.find_company_memory(name) for name in companies
        )
        initial_companies = tuple(
            fallback_company_scope(name, memory=memory)
            for name, memory in zip(companies, memories, strict=True)
        )
        initial_terms = fallback_technology_terms(technology) if technology else ()
        identity = draft_id or make_scope_draft_id(uuid.uuid4().hex)
        initial = ScopeDraft(
            draft_id=identity,
            revision=1,
            status=ScopeDraftStatus.DRAFT,
            publication_start=publication_start,
            publication_end=publication_end,
            companies=initial_companies,
            technology_input=technology,
            technology_terms=initial_terms,
        )
        return self.repository.create(initial)

    async def expand_draft(
        self,
        draft_id: str,
        *,
        expected_revision: int,
    ) -> ScopeDraft:
        initial = self.repository.get(draft_id)
        if initial.revision != expected_revision:
            raise ScopePreparationError(
                f"scope draft revision changed: expected {expected_revision}, "
                f"found {initial.revision}"
            )
        if initial.status != ScopeDraftStatus.DRAFT:
            raise ScopePreparationError("only a DRAFT scope can be expanded")

        companies = tuple(company.input_name for company in initial.companies)
        memories = tuple(
            CompanyProfileMemory(profile_version=0, company=company)
            for company in initial.companies
        )
        technology = initial.technology_input
        initial_terms = initial.technology_terms
        expanding_revision = initial.revision + 1
        expanding = initial.model_copy(
            update={
                "revision": expanding_revision,
                "status": ScopeDraftStatus.EXPANDING,
            }
        )
        self.repository.update(expanding, expected_revision=initial.revision)

        semaphore = asyncio.Semaphore(self.max_company_concurrency)

        async def expand_company(index: int):
            async with semaphore:
                return await asyncio.wait_for(
                    self.expansion.expand_company(
                        companies[index], memory=memories[index]
                    ),
                    timeout=self.object_timeout_seconds,
                )

        company_results = asyncio.gather(
            *(expand_company(index) for index in range(len(companies))),
            return_exceptions=True,
        )
        if technology:
            technology_result = asyncio.create_task(
                asyncio.wait_for(
                    self.expansion.expand_technology(technology),
                    timeout=self.object_timeout_seconds,
                )
            )
        else:
            technology_result = None

        resolved_companies = await company_results
        if technology_result is None:
            resolved_terms = None
        else:
            try:
                resolved_terms = await technology_result
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                resolved_terms = exc
        limitations: list[ScopeDraftLimitation] = []
        final_companies: list[CompanyScopeDraft] = []
        for name, memory, result in zip(
            companies, memories, resolved_companies, strict=True
        ):
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, BaseException):
                final_companies.append(fallback_company_scope(name, memory=memory))
                limitations.append(
                    ScopeDraftLimitation(
                        code="COMPANY_EXPANSION_FAILED",
                        object_key=name,
                        message="公司名称扩展失败，已保留历史名称和用户原始输入，请手工审查补充。",
                    )
                )
                continue
            final_companies.append(result)
            base_count = len(memory.company.names)
            if len(result.names) - base_count >= 40:
                limitations.append(
                    ScopeDraftLimitation(
                        code="COMPANY_CANDIDATE_LIMIT_REACHED",
                        object_key=name,
                        message="公司名称候选达到本地审查上限 40 项，模型的其余建议未进入草稿。",
                    )
                )

        if isinstance(resolved_terms, BaseException):
            final_terms = initial_terms
            limitations.append(
                ScopeDraftLimitation(
                    code="TECHNOLOGY_EXPANSION_FAILED",
                    object_key=technology or "technology",
                    message="技术词扩展失败，已保留用户原始输入，请手工补充中英文检索词。",
                )
            )
        elif resolved_terms is None:
            final_terms = ()
        else:
            final_terms = resolved_terms
            if len(final_terms) >= 61:
                limitations.append(
                    ScopeDraftLimitation(
                        code="TECHNOLOGY_CANDIDATE_LIMIT_REACHED",
                        object_key=technology or "technology",
                        message="技术词候选达到本地审查上限 60 个新增词，其余建议未进入草稿。",
                    )
                )

        reviewable = ScopeDraft(
            draft_id=initial.draft_id,
            revision=expanding_revision + 1,
            status=ScopeDraftStatus.AWAITING_CONFIRMATION,
            publication_start=initial.publication_start,
            publication_end=initial.publication_end,
            companies=tuple(final_companies),
            technology_input=technology,
            technology_terms=tuple(final_terms),
            limitations=tuple(limitations),
        )
        return self.repository.update(
            reviewable, expected_revision=expanding_revision
        )


def _validate_company_inputs(values: tuple[str, ...]) -> tuple[str, ...]:
    if len(values) > 50:
        raise ScopePreparationError("at most 50 companies are allowed")
    cleaned = tuple(value.strip() for value in values)
    if any(not value for value in cleaned):
        raise ScopePreparationError("company names must not be blank")
    normalized = [normalize_scope_text(value) for value in cleaned]
    if len(normalized) != len(set(normalized)):
        raise ScopePreparationError("company inputs must be unique after normalization")
    return cleaned


__all__ = [
    "ScopeDraftPreparationService",
    "ScopePreparationError",
]
