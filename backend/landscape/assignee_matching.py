"""One deterministic assignee resolver for filtering and company attribution.

The resolver deliberately distinguishes exact legal-name/alias equality from an
explicit user-selected group-name text scope.  A GROUP hit is evidence of a
name-pattern match only; it is never presented as a corporate-control finding.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Sequence

from .schemas import AssigneeScope, CompetitorInput


class AssigneeResolutionStatus(StrEnum):
    CONFIRMED_ALIAS = "CONFIRMED_ALIAS"
    CONFIRMED_GROUP_SCOPE = "CONFIRMED_GROUP_SCOPE"
    AMBIGUOUS = "AMBIGUOUS"
    UNMATCHED = "UNMATCHED"


@dataclass(frozen=True)
class AssigneeResolution:
    status: AssigneeResolutionStatus
    competitor: CompetitorInput | None = None
    matched_alias: str | None = None

    @property
    def is_confirmed(self) -> bool:
        return self.status in {
            AssigneeResolutionStatus.CONFIRMED_ALIAS,
            AssigneeResolutionStatus.CONFIRMED_GROUP_SCOPE,
        }


_CJK = re.compile(r"^[\u3400-\u9fff]+$")
_LEGAL_ENTITY_SUFFIXES = (
    "有限责任公司",
    "股份有限公司",
    "有限公司",
    "公司",
)


def normalize_assignee_name(value: str) -> str:
    """Return the conservative equality key for an observed assignee."""

    value = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(value.split())


def resolve_competitor_assignee(
    assignee: str | None,
    competitors: Sequence[CompetitorInput],
) -> AssigneeResolution:
    """Resolve one assignee against a validated runtime competitor registry.

    Exact normalized equality always wins.  Only after no exact owner exists
    may an explicitly GROUP-scoped runtime name or alias act as a textual
    anchor.  GROUP remains an auditable name-pattern scope, never a claim of
    corporate control or ownership.
    """

    if not assignee:
        return AssigneeResolution(AssigneeResolutionStatus.UNMATCHED)

    exact_matches = _matches_exact(assignee, competitors)
    if exact_matches:
        return _resolution_from_matches(
            exact_matches,
            AssigneeResolutionStatus.CONFIRMED_ALIAS,
        )

    group_matches = [
        (competitor, anchor)
        for competitor in competitors
        if competitor.assignee_scope == AssigneeScope.GROUP
        for anchor in (competitor.name, *competitor.aliases)
        if _matches_group_anchor(assignee, anchor)
    ]
    if group_matches:
        return _resolution_from_matches(
            group_matches,
            AssigneeResolutionStatus.CONFIRMED_GROUP_SCOPE,
        )
    return AssigneeResolution(AssigneeResolutionStatus.UNMATCHED)


def _matches_exact(
    observed: str,
    competitors: Sequence[CompetitorInput],
) -> list[tuple[CompetitorInput, str]]:
    observed_key = normalize_assignee_name(observed)
    if not observed_key:
        return []
    return [
        (competitor, name)
        for competitor in competitors
        for name in (competitor.name, *competitor.aliases)
        if observed_key == normalize_assignee_name(name)
    ]


def _resolution_from_matches(
    matches: Sequence[tuple[CompetitorInput, str]],
    status: AssigneeResolutionStatus,
) -> AssigneeResolution:
    by_competitor: dict[str, tuple[CompetitorInput, list[str]]] = {}
    for competitor, alias in matches:
        key = normalize_assignee_name(competitor.name)
        if key not in by_competitor:
            by_competitor[key] = (competitor, [])
        by_competitor[key][1].append(alias)
    if len(by_competitor) != 1:
        return AssigneeResolution(AssigneeResolutionStatus.AMBIGUOUS)
    competitor, aliases = next(iter(by_competitor.values()))
    matched_alias = min(
        aliases,
        key=lambda value: (
            -len(normalize_assignee_name(value)),
            value.casefold(),
            value,
        ),
    )
    return AssigneeResolution(
        status=status,
        competitor=competitor,
        matched_alias=matched_alias,
    )


def _matches_group_anchor(observed: str, anchor: str) -> bool:
    normalized_anchor = normalize_assignee_name(anchor)
    if not normalized_anchor:
        return False
    compact_anchor = _compact_group_text(normalized_anchor)
    compact_observed = _compact_group_text(normalize_assignee_name(observed))
    if not compact_anchor or not compact_observed:
        return False
    if _CJK.fullmatch(compact_anchor):
        if len(compact_anchor) < 2 or compact_anchor.endswith(
            _LEGAL_ENTITY_SUFFIXES
        ):
            return False
        return (
            compact_anchor in compact_observed
            and compact_observed != compact_anchor
            and compact_observed.endswith(_LEGAL_ENTITY_SUFFIXES)
        )
    word_anchor = _word_normalize(normalized_anchor)
    word_observed = _word_normalize(observed)
    return bool(
        word_anchor
        and re.search(
            rf"(?<![a-z0-9]){re.escape(word_anchor)}(?![a-z0-9])",
            word_observed,
        )
    )


def _compact_group_text(value: str) -> str:
    return re.sub(r"[^a-z0-9\u3400-\u9fff]+", "", value.casefold())


def _word_normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"[^\w\u3400-\u9fff]+", " ", normalized)
    return " ".join(normalized.split())


__all__ = [
    "AssigneeResolution",
    "AssigneeResolutionStatus",
    "normalize_assignee_name",
    "resolve_competitor_assignee",
]
