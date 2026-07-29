from __future__ import annotations

import re
import unicodedata

from .schemas import (
    AnalysisMode,
    CompetitorAliasPlan,
    CompetitorAliasPlanDraft,
    CompetitorAliasResolution,
    CompetitorAliasResolutionDraft,
    CompetitorInput,
    LandscapePlannedQuery,
    LandscapeQueryPlan,
    LandscapeScope,
    TechnicalDirectionExpansion,
)
from idea.agent_schemas import register_agent_output_model
from idea.model_client import StructuredModelClient


ALIAS_AGENT_NAME = "patent-landscape-competitor-aliaser"
TECHNICAL_DIRECTION_AGENT_NAME = "patent-landscape-direction-expander"
ALIAS_PROMPT = """
For every supplied target company (the company whose patent assignees will be searched), identify
patent-assignee search aliases: common Chinese and English names, full corporate names,
abbreviations, and well-known historical names. Return exactly one item per supplied primary_name
and copy each primary_name exactly. Do not introduce a different corporate group, subsidiary,
affiliate, product brand, or guessed legal entity. Use at most 12 aliases per company. source must
be MODEL_INFERRED. This output expands search terms and is not a legal entity verification.
"""
TECHNICAL_DIRECTION_PROMPT = """
Expand the supplied patent technology direction into precise search terminology. Copy original_term
exactly. Return 2-8 concise Chinese patent-search terms in chinese_terms and 2-8 concise English
patent-search terms in english_terms. Include direct translations, established technical synonyms,
and closely equivalent patent terminology, but do not broaden into a different technology. Terms
must describe technical means rather than market language. source must be MODEL_INFERRED.
"""


class CompetitorAliasError(RuntimeError):
    pass


class CompetitorAliasService:
    def __init__(self, model: StructuredModelClient):
        register_agent_output_model(ALIAS_AGENT_NAME, CompetitorAliasPlanDraft)
        self.model = model

    async def resolve(self, competitors: list[CompetitorInput]) -> CompetitorAliasPlan:
        if not competitors:
            raise CompetitorAliasError("at least one competitor is required")
        result = await self.model.complete(
            ALIAS_AGENT_NAME,
            system_prompt=ALIAS_PROMPT,
            input_payload={"competitors": [{"primary_name": item.name} for item in competitors]},
        )
        output = result.output
        if not isinstance(
            output,
            (CompetitorAliasPlan, CompetitorAliasPlanDraft),
        ):
            raise CompetitorAliasError("competitor aliaser returned the wrong schema")
        output = _reconcile_alias_plan(output, competitors)
        validate_alias_plan(output, competitors)
        return output


class TechnicalDirectionError(RuntimeError):
    pass


class TechnicalDirectionService:
    def __init__(self, model: StructuredModelClient):
        register_agent_output_model(
            TECHNICAL_DIRECTION_AGENT_NAME, TechnicalDirectionExpansion
        )
        self.model = model

    async def expand(self, direction: str) -> TechnicalDirectionExpansion:
        result = await self.model.complete(
            TECHNICAL_DIRECTION_AGENT_NAME,
            system_prompt=TECHNICAL_DIRECTION_PROMPT,
            input_payload={"original_term": direction},
        )
        output = result.output
        if not isinstance(output, TechnicalDirectionExpansion):
            raise TechnicalDirectionError("direction expander returned the wrong schema")
        if output.original_term != direction:
            raise TechnicalDirectionError("direction expander changed the original term")
        if not any(_contains_cjk(term) for term in output.chinese_terms):
            raise TechnicalDirectionError("direction expansion contains no Chinese search term")
        if not any(_contains_latin(term) for term in output.english_terms):
            raise TechnicalDirectionError("direction expansion contains no English search term")
        return output


def validate_alias_plan(plan: CompetitorAliasPlan, inputs: list[CompetitorInput]) -> None:
    expected = {item.name.casefold(): item.name for item in inputs}
    actual = [item.primary_name.casefold() for item in plan.competitors]
    if len(actual) != len(set(actual)):
        raise CompetitorAliasError("competitor alias output contains duplicate primary names")
    if set(actual) != set(expected):
        raise CompetitorAliasError("competitor alias output changed the requested entities")
    primary_names = set(expected)
    for item in plan.competitors:
        for alias in item.aliases:
            if alias.casefold() in primary_names and alias.casefold() != item.primary_name.casefold():
                raise CompetitorAliasError("an alias collides with another requested competitor")


def _reconcile_alias_plan(
    output: CompetitorAliasPlan | CompetitorAliasPlanDraft,
    inputs: list[CompetitorInput],
) -> CompetitorAliasPlan:
    """Bind model rows back to user-owned company names before validation.

    A model may echo a common alias as ``primary_name`` (for example NVIDIA
    for 英伟达), omit one row, or return duplicate rows. Those are model
    quality issues, not reasons to discard every valid alias in the run.
    Primary names and missing rows are repaired deterministically here.
    """

    expected_keys = {
        _identity_key(input_item.name): index
        for index, input_item in enumerate(inputs)
    }
    primary_keys = set(expected_keys)
    matched: dict[
        int, CompetitorAliasResolutionDraft | CompetitorAliasResolution
    ] = {}

    for item in output.competitors:
        target_index = expected_keys.get(_identity_key(item.primary_name))
        if target_index is None:
            alias_targets = {
                expected_keys[_identity_key(alias)]
                for alias in item.aliases
                if _identity_key(alias) in expected_keys
            }
            if len(alias_targets) == 1:
                target_index = alias_targets.pop()
        if target_index is None and len(inputs) == 1 and not matched:
            target_index = 0
        if target_index is None or target_index in matched:
            continue
        matched[target_index] = item

    reconciled: list[CompetitorAliasResolution] = []
    for index, input_item in enumerate(inputs):
        item = matched.get(index)
        if item is None:
            reconciled.append(
                CompetitorAliasResolution(
                    primary_name=input_item.name,
                    aliases=[],
                    source="PRIMARY_NAME_FALLBACK",
                )
            )
            continue
        aliases: list[str] = []
        seen: set[str] = set()
        input_key = _identity_key(input_item.name)
        for raw_alias in [item.primary_name, *item.aliases]:
            alias = " ".join(raw_alias.split())
            key = _identity_key(alias)
            if not alias or key == input_key:
                continue
            # Never let a model alias silently become another user-owned
            # company in a multi-company request.
            if key in primary_keys and key != input_key:
                continue
            if key in seen:
                continue
            seen.add(key)
            aliases.append(alias)
            if len(aliases) == 12:
                break
        reconciled.append(
            CompetitorAliasResolution(
                primary_name=input_item.name,
                aliases=aliases,
                source="MODEL_INFERRED",
            )
        )
    return CompetitorAliasPlan(competitors=reconciled)


def _identity_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^\w\u3400-\u9fff]+", "", normalized, flags=re.UNICODE)


def fallback_alias_plan(competitors: list[CompetitorInput]) -> CompetitorAliasPlan:
    return CompetitorAliasPlan(
        competitors=[
            CompetitorAliasResolution(
                primary_name=item.name,
                aliases=[],
                source="PRIMARY_NAME_FALLBACK",
            )
            for item in competitors
        ]
    )


def scope_with_alias_plan(scope: LandscapeScope, plan: CompetitorAliasPlan) -> LandscapeScope:
    """Build the validated runtime registry used for query, filter and assignment.

    The model may contribute cross-language aliases for the user-selected
    competitor, while the primary name and assignee-scope setting remain
    user-owned.  The resulting registry is intentionally shared by every
    downstream assignee decision so the candidate set and company partition use
    the same contract.
    """
    resolved = {
        item.primary_name.casefold(): item for item in plan.competitors
    }
    return scope.model_copy(
        update={
            "competitors": [
                CompetitorInput(
                    name=competitor.name,
                    aliases=_unique(
                        [
                            *competitor.aliases,
                            *resolved[competitor.name.casefold()].aliases,
                        ]
                    ),
                    assignee_scope=competitor.assignee_scope,
                )
                for competitor in scope.competitors
            ]
        }
    )


def build_deterministic_query_plan(
    scope: LandscapeScope,
    direction_expansion: TechnicalDirectionExpansion | None = None,
) -> LandscapeQueryPlan:
    """Build a bounded plan while guaranteeing coverage of every user-owned anchor."""
    direction = scope.technology_direction or ""
    if direction and direction_expansion is None:
        raise ValueError("technical direction expansion is required")
    direction_group = ""
    direction_terms: list[str] = []
    english_terms: list[str] = []
    if direction_expansion is not None:
        chinese = _unique([direction, *direction_expansion.chinese_terms])
        english_terms = _unique(direction_expansion.english_terms)
        direction_terms = _unique([*chinese, *english_terms])
        direction_group = _bounded_or(
            [*chinese, *english_terms], max_names=11, max_chars=300
        )

    candidates: list[tuple[str, str, str]] = []
    if scope.competitors:
        for competitor in scope.competitors:
            names = _unique([competitor.name, *competitor.aliases])
            name_group = _bounded_or(
                names, max_names=8, max_chars=180 if direction_group else 440
            )
            if direction_group:
                text = f"({direction_group}) AND ({name_group})"
                candidates.append(
                    (
                        text,
                        _language(text),
                        f"中英文技术词组 × 友商：{competitor.name}",
                    )
                )
            else:
                candidates.append(
                    (
                        name_group,
                        _language(name_group),
                        f"友商中英文别名组：{competitor.name}",
                    )
                )
    else:
        candidates.append(
            (direction_group, _language(direction_group), "中英文技术词组")
        )
    unique: list[LandscapePlannedQuery] = []
    seen: set[str] = set()
    for text, language, rationale in candidates:
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(
            LandscapePlannedQuery(query_text=text, language=language, rationale=rationale)
        )
        if len(unique) == 40:
            break
    return LandscapeQueryPlan(
        direction_terms=direction_terms,
        direction_english_terms=english_terms,
        queries=unique,
    )


def validate_query_plan_scope(plan: LandscapeQueryPlan, scope: LandscapeScope) -> None:
    """Fail closed if a plan does not retain the user-owned scope anchor."""
    searchable = " ".join(item.query_text for item in plan.queries).casefold()
    if scope.mode in {AnalysisMode.TECHNOLOGY, AnalysisMode.TECHNOLOGY_COMPETITOR}:
        direction = (scope.technology_direction or "").casefold()
        if direction not in searchable:
            raise ValueError("query plan does not contain the technology direction")
        if not plan.direction_english_terms or not any(
            term.casefold() in searchable for term in plan.direction_english_terms
        ):
            raise ValueError("query plan does not contain an English technology direction")
    if scope.mode in {AnalysisMode.COMPETITOR, AnalysisMode.TECHNOLOGY_COMPETITOR}:
        for competitor in scope.competitors:
            names = [
                value.casefold() for value in [competitor.name, *competitor.aliases]
            ]
            matching = [
                item.query_text.casefold()
                for item in plan.queries
                if any(name in item.query_text.casefold() for name in names)
            ]
            if not matching:
                raise ValueError(
                    f"query plan does not contain competitor: {competitor.name}"
                )
            if scope.mode == AnalysisMode.TECHNOLOGY_COMPETITOR and not any(
                direction in query
                and any(
                    term.casefold() in query
                    for term in plan.direction_english_terms
                )
                for query in matching
            ):
                raise ValueError(
                    "combined query does not retain bilingual direction for "
                    f"competitor: {competitor.name}"
                )


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = " ".join(raw.split())
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _bounded_or(
    values: list[str], *, prefix: str = "", max_names: int, max_chars: int
) -> str:
    parts: list[str] = []
    length = 0
    for value in values[:max_names]:
        escaped = value.replace('"', " ").strip()[: max_chars - len(prefix) - 2]
        part = f'{prefix}"{escaped}"'
        added = len(part) + (4 if parts else 0)
        if parts and length + added > max_chars:
            break
        parts.append(part)
        length += added
    return " OR ".join(parts)


def _language(value: str) -> str:
    has_cjk = _contains_cjk(value)
    has_latin = _contains_latin(value)
    if has_cjk and has_latin:
        return "mixed"
    return "zh" if has_cjk else "en"


def _contains_cjk(value: str) -> bool:
    return any("\u3400" <= character <= "\u9fff" for character in value)


def _contains_latin(value: str) -> bool:
    return any(character.isascii() and character.isalpha() for character in value)
