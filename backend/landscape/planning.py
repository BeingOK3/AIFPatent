from __future__ import annotations

from .schemas import (
    AnalysisMode,
    LandscapePlannedQuery,
    LandscapeQueryPlan,
    LandscapeScope,
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
    if scope.mode == AnalysisMode.TECHNOLOGY:
        direction = (scope.technology_direction or "").casefold()
        if direction not in searchable:
            raise ValueError("query plan does not contain the technology direction")
    else:
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
