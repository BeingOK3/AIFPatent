from __future__ import annotations

from .schemas import (
    AnalysisMode,
    CompetitorAliasPlan,
    CompetitorAliasResolution,
    CompetitorInput,
    LandscapePlannedQuery,
    LandscapeQueryPlan,
    LandscapeScope,
)
from idea.agent_schemas import register_agent_output_model
from idea.model_client import StructuredModelClient


ALIAS_AGENT_NAME = "patent-landscape-competitor-aliaser"
ALIAS_PROMPT = """
For every supplied competitor primary_name, identify patent-assignee search aliases: common Chinese
and English names, full corporate names, abbreviations, and well-known historical names. Return
exactly one item per supplied primary_name and copy each primary_name exactly. Do not introduce a
different corporate group, subsidiary, affiliate, product brand, or guessed legal entity. Use at
most 12 aliases per competitor. source must be MODEL_INFERRED. This output expands search terms and
is not a legal entity verification.
"""


class CompetitorAliasError(RuntimeError):
    pass


class CompetitorAliasService:
    def __init__(self, model: StructuredModelClient):
        register_agent_output_model(ALIAS_AGENT_NAME, CompetitorAliasPlan)
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
        if not isinstance(output, CompetitorAliasPlan):
            raise CompetitorAliasError("competitor aliaser returned the wrong schema")
        output = CompetitorAliasPlan(
            competitors=[
                item.model_copy(
                    update={
                        "aliases": [
                            alias
                            for alias in item.aliases
                            if alias.casefold() != item.primary_name.casefold()
                        ]
                    }
                )
                for item in output.competitors
            ]
        )
        validate_alias_plan(output, competitors)
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
    resolved = {
        item.primary_name.casefold(): item for item in plan.competitors
    }
    return scope.model_copy(
        update={
            "competitors": [
                CompetitorInput(
                    name=competitor.name,
                    aliases=resolved[competitor.name.casefold()].aliases,
                )
                for competitor in scope.competitors
            ]
        }
    )


def build_deterministic_query_plan(scope: LandscapeScope) -> LandscapeQueryPlan:
    """Build a safe fallback plan without asking a model to alter scope constraints."""
    direction = scope.technology_direction or ""
    competitor_names = [
        value
        for competitor in scope.competitors
        for value in [competitor.name, *competitor.aliases]
    ]
    candidates: list[tuple[str, str, str]] = []
    if direction:
        candidates.extend(
            [
                (direction, _language(direction), "技术方向原始检索词"),
                (f'"{direction}"', _language(direction), "技术方向精确短语检索"),
            ]
        )
    for name in competitor_names:
        if direction:
            candidates.append(
                (f'{direction} "{name}"', _language(f"{direction} {name}"), "技术方向与确认友商联合检索")
            )
        else:
            candidates.extend(
                [
                    (name, _language(name), "确认友商名称检索"),
                    (f'assignee:"{name}"', _language(name), "确认友商申请人字段检索"),
                ]
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
        if len(unique) == 6:
            break
    if len(unique) < 2:
        seed = direction or competitor_names[0]
        unique.append(
            LandscapePlannedQuery(
                query_text=f'patent "{seed}"',
                language=_language(seed),
                rationale="补充专利文献检索",
            )
        )
    direction_terms = [direction] if direction else []
    return LandscapeQueryPlan(direction_terms=direction_terms, queries=unique)


def validate_query_plan_scope(plan: LandscapeQueryPlan, scope: LandscapeScope) -> None:
    """Fail closed if a plan does not retain the user-owned scope anchor."""
    searchable = " ".join(item.query_text for item in plan.queries).casefold()
    if scope.mode in {AnalysisMode.TECHNOLOGY, AnalysisMode.TECHNOLOGY_COMPETITOR}:
        direction = (scope.technology_direction or "").casefold()
        if direction not in searchable:
            raise ValueError("query plan does not contain the technology direction")
    if scope.mode in {AnalysisMode.COMPETITOR, AnalysisMode.TECHNOLOGY_COMPETITOR}:
        names = [
            value.casefold()
            for competitor in scope.competitors
            for value in [competitor.name, *competitor.aliases]
        ]
        if not any(name in searchable for name in names):
            raise ValueError("query plan does not contain a confirmed competitor name")


def _language(value: str) -> str:
    has_cjk = any("\u3400" <= character <= "\u9fff" for character in value)
    has_latin = any(character.isascii() and character.isalpha() for character in value)
    if has_cjk and has_latin:
        return "mixed"
    return "zh" if has_cjk else "en"
