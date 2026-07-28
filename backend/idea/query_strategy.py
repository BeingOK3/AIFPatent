from __future__ import annotations

import re

from .agent_schemas import PlannedQuery, QueryPlannerOutput


MAX_EXECUTABLE_QUERIES = 24
MAX_GROUPS_PER_QUERY = 2

_FIELD_PREFIX_PATTERN = re.compile(
    r"\b(?:IPC|CPC|ICL|CLASS|CLASSIFICATION)\s*:\s*",
    flags=re.IGNORECASE,
)
_BOOLEAN_AND_PATTERN = re.compile(r"\s+\bAND\b\s+", flags=re.IGNORECASE)
_PAREN_GROUP_PATTERN = re.compile(r"\(([^()]*)\)")
_SPACE_PATTERN = re.compile(r"\s+")


def expand_recall_plan(
    plan: QueryPlannerOutput,
    *,
    max_queries: int = MAX_EXECUTABLE_QUERIES,
) -> QueryPlannerOutput:
    """Turn model-authored queries into bounded high-recall executable queries.

    Models are useful for discovering terminology, but provider query dialects are
    a poor control surface. Over-constrained expressions are split into one- or
    two-concept searches so a single missing synonym cannot suppress every result.
    """

    drafts: list[tuple[int, str, str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    def add(
        *,
        round_number: int,
        query_type: str,
        language: str,
        query_text: str,
        rationale: str,
    ) -> None:
        text = canonical_query_text(query_text)
        identity = (query_type, language, text.casefold())
        if len(text) < 3 or identity in seen or len(drafts) >= max_queries:
            return
        seen.add(identity)
        drafts.append((round_number, query_type, language, text, rationale))

    # Preserve already broad model queries and split over-constrained ones. Each
    # source query gets a fair share before term-group fallbacks are appended.
    per_source_limit = max(1, max_queries // max(1, len(plan.queries)))
    for source in plan.queries:
        variants = split_for_recall(source.query_text)
        for variant in variants[:per_source_limit]:
            add(
                round_number=min(source.round_number, 2),
                query_type=source.query_type,
                language=source.language,
                query_text=variant,
                rationale=f"{source.rationale}；宽召回拆分，最多保留两个概念组。",
            )

    # Deterministic single-concept fallbacks ensure the run is not wholly
    # dependent on the model's Boolean syntax.
    for group in plan.term_groups:
        for language, terms in (("zh", group.zh_terms), ("en", group.en_terms)):
            add(
                round_number=2,
                query_type="technical_means",
                language=language,
                query_text=_or_expression(terms[:5]),
                rationale=f"概念“{group.concept}”的单概念宽召回兜底。",
            )

    # Classification identifiers are searched independently. Provider adapters
    # may compile them further, but raw field prefixes are never sent upstream.
    if plan.ipc_cpc_candidates:
        add(
            round_number=2,
            query_type="classification",
            language="mixed",
            query_text=_or_expression(plan.ipc_cpc_candidates[:8]),
            rationale="IPC/CPC 候选的独立宽召回，不与多个技术概念强制相交。",
        )

    queries = [
        PlannedQuery(
            query_id=f"Q{index}",
            round_number=round_number,
            query_type=query_type,
            language=language,
            query_text=query_text,
            rationale=rationale,
        )
        for index, (round_number, query_type, language, query_text, rationale) in enumerate(
            drafts, start=1
        )
    ]
    return plan.model_copy(update={"queries": queries})


def split_for_recall(query_text: str) -> list[str]:
    text = canonical_query_text(query_text)
    groups = [
        canonical_query_text(group)
        for group in _PAREN_GROUP_PATTERN.findall(text)
        if canonical_query_text(group)
    ]
    if len(groups) <= MAX_GROUPS_PER_QUERY and len(_BOOLEAN_AND_PATTERN.findall(text)) < 2:
        return [text]
    if not groups:
        parts = [
            canonical_query_text(part)
            for part in _BOOLEAN_AND_PATTERN.split(text)
            if canonical_query_text(part)
        ]
        groups = parts
    if not groups:
        return [text]

    variants: list[str] = []
    for index in range(0, len(groups), MAX_GROUPS_PER_QUERY):
        chunk = groups[index : index + MAX_GROUPS_PER_QUERY]
        variants.append(" AND ".join(f"({item})" for item in chunk))
    # Single groups are deliberately retained: they recover documents whose
    # title/abstract uses only one side of an unfamiliar terminology pair.
    variants.extend(f"({group})" for group in groups)
    return list(dict.fromkeys(variants))


def compile_provider_query(query_text: str, provider_name: str) -> str:
    """Compile the canonical subset accepted by all current providers."""

    text = canonical_query_text(query_text)
    if provider_name == "exa_mcp":
        # Exa is semantic and performs better without Boolean operators.
        text = re.sub(r"\b(?:AND|OR)\b", " ", text, flags=re.IGNORECASE)
        text = text.replace("(", " ").replace(")", " ").replace('"', " ")
        return _SPACE_PATTERN.sub(" ", text).strip()
    return text


def canonical_query_text(query_text: str) -> str:
    text = _FIELD_PREFIX_PATTERN.sub("", query_text)
    text = text.replace("“", '"').replace("”", '"')
    return _SPACE_PATTERN.sub(" ", text).strip(" ;")


def _or_expression(terms: list[str]) -> str:
    values = [canonical_query_text(term).strip('"() ') for term in terms]
    values = list(dict.fromkeys(value for value in values if value))
    if not values:
        return ""
    rendered = [f'"{value}"' if " " in value else value for value in values]
    return "(" + " OR ".join(rendered) + ")"
