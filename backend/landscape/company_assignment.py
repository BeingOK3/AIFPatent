from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Sequence

from idea.merge import MergedHit, normalize_publication_number

from .schemas import (
    AnalysisMode,
    CompanyAssignment,
    CompetitorInput,
    LandscapeScope,
    NormalizedCompany,
)


UNKNOWN_COMPANY = NormalizedCompany(
    company_id="UNKNOWN",
    canonical_name="UNKNOWN",
    aliases=[],
)

_NON_WORD = re.compile(r"[^\w]+", re.UNICODE)
_ASCII_ID_PART = re.compile(r"[^A-Z0-9]+")


class CompanyAssignmentValidationError(ValueError):
    """Raised when company assignments do not form an exact partition of U."""


@dataclass(frozen=True)
class CompanyAssignmentResult:
    companies: tuple[NormalizedCompany, ...]
    assignments: tuple[CompanyAssignment, ...]


@dataclass(frozen=True)
class _CompetitorIdentity:
    company: NormalizedCompany
    confirmed_names: tuple[str, ...]


def assign_companies(
    eligible_hits: Iterable[MergedHit],
    scope: LandscapeScope,
    *,
    user_confirmed_competitors: Sequence[CompetitorInput] | None = None,
) -> CompanyAssignmentResult:
    """Assign every unique eligible publication to one deterministic company.

    Competitor-containing scopes only recognize names explicitly supplied by the
    user. Competitor modes deliberately require a separate
    ``user_confirmed_competitors`` argument so a search-only effective scope
    containing model-inferred aliases cannot be used by accident. Pure technology
    scopes group equal normalized source assignee strings; they never infer
    parents, subsidiaries, abbreviations, or similar names.
    """

    hits = _ordered_unique_hits(eligible_hits)
    if scope.mode == AnalysisMode.TECHNOLOGY:
        result = _assign_normalized_names(hits)
    else:
        if user_confirmed_competitors is None:
            raise CompanyAssignmentValidationError(
                "competitor assignment requires raw user_confirmed_competitors"
            )
        result = _assign_confirmed_competitors(
            hits,
            user_confirmed_competitors,
        )
    validate_company_assignments(hits, result.companies, result.assignments)
    return result


def validate_company_assignments(
    eligible_hits: Iterable[MergedHit],
    companies: Sequence[NormalizedCompany],
    assignments: Sequence[CompanyAssignment],
) -> None:
    """Validate U coverage, primary uniqueness, and company references."""

    expected: list[str] = []
    for hit in eligible_hits:
        publication = normalize_publication_number(hit.publication_number)
        if publication is None:
            raise CompanyAssignmentValidationError(
                "every eligible hit must have a publication number"
            )
        expected.append(publication)
    if len(expected) != len(set(expected)):
        raise CompanyAssignmentValidationError(
            "eligible publications must be unique after normalization"
        )

    company_ids = [company.company_id for company in companies]
    if len(company_ids) != len(set(company_ids)):
        raise CompanyAssignmentValidationError("company IDs must be unique")
    company_id_set = set(company_ids)
    company_by_id = {company.company_id: company for company in companies}

    alias_owners: dict[str, str] = {}
    for company in companies:
        if company.company_id == "UNKNOWN":
            continue
        for name in (company.canonical_name, *company.aliases):
            key = normalize_assignee_name(name)
            owner = alias_owners.setdefault(key, company.company_id)
            if owner != company.company_id:
                raise CompanyAssignmentValidationError(
                    f"confirmed alias belongs to multiple companies: {name}"
                )

    actual = [
        normalize_publication_number(assignment.publication_number)
        for assignment in assignments
    ]
    if any(publication is None for publication in actual):
        raise CompanyAssignmentValidationError(
            "every assignment must have a publication number"
        )
    duplicates = sorted(
        publication
        for publication in set(actual)
        if actual.count(publication) > 1
    )
    if duplicates:
        raise CompanyAssignmentValidationError(
            f"duplicate primary assignments: {', '.join(duplicates)}"
        )

    missing = sorted(set(expected) - set(actual))
    invented = sorted(set(actual) - set(expected))
    if missing or invented:
        details = []
        if missing:
            details.append(f"missing: {', '.join(missing)}")
        if invented:
            details.append(f"outside U: {', '.join(invented)}")
        raise CompanyAssignmentValidationError(
            "assignments must cover U exactly (" + "; ".join(details) + ")"
        )

    unknown_references = sorted(
        {
            assignment.primary_company_id
            for assignment in assignments
            if assignment.primary_company_id not in company_id_set
        }
    )
    if unknown_references:
        raise CompanyAssignmentValidationError(
            "assignments reference unknown companies: "
            + ", ".join(unknown_references)
        )
    for assignment in assignments:
        if assignment.status != "CONFIRMED_ALIAS":
            continue
        company = company_by_id[assignment.primary_company_id]
        allowed = {
            normalize_assignee_name(name)
            for name in (company.canonical_name, *company.aliases)
        }
        if normalize_assignee_name(assignment.matched_alias or "") not in allowed:
            raise CompanyAssignmentValidationError(
                "matched_alias is not registered to primary company: "
                f"{assignment.publication_number}"
            )
        if not _is_exact_name_match(
            assignment.observed_assignee or "",
            assignment.matched_alias or "",
        ):
            raise CompanyAssignmentValidationError(
                "matched_alias does not match observed assignee: "
                f"{assignment.publication_number}"
            )


def normalize_assignee_name(value: str) -> str:
    """Return the conservative equality key used by pure technology mode."""

    value = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(value.split())


def _ordered_unique_hits(hits: Iterable[MergedHit]) -> tuple[MergedHit, ...]:
    by_publication: dict[str, MergedHit] = {}
    for hit in hits:
        publication = normalize_publication_number(hit.publication_number)
        if publication is None:
            raise CompanyAssignmentValidationError(
                "every eligible hit must have a publication number"
            )
        if publication in by_publication:
            raise CompanyAssignmentValidationError(
                f"eligible publications must be unique after normalization: {publication}"
            )
        by_publication[publication] = hit
    return tuple(by_publication[key] for key in sorted(by_publication))


def _assign_confirmed_competitors(
    hits: Sequence[MergedHit],
    competitors: Sequence[CompetitorInput],
) -> CompanyAssignmentResult:
    raw_identities = [
        (competitor.name, tuple([competitor.name, *competitor.aliases]))
        for competitor in competitors
    ]
    ids = _stable_competitor_ids(name for name, _ in raw_identities)
    identities = tuple(
        _CompetitorIdentity(
            company=NormalizedCompany(
                company_id=ids[name],
                canonical_name=name,
                aliases=[
                    alias
                    for alias in confirmed_names[1:]
                    if normalize_assignee_name(alias)
                    != normalize_assignee_name(name)
                ],
            ),
            confirmed_names=confirmed_names,
        )
        for name, confirmed_names in raw_identities
    )

    assignments: list[CompanyAssignment] = []
    needs_unknown = False
    for hit in hits:
        publication = normalize_publication_number(hit.publication_number)
        primary, sources_conflict = _source_assignee(hit)
        co_assignees: list[str] = []
        if primary is None:
            needs_unknown = True
            assignments.append(
                CompanyAssignment(
                    publication_number=publication,
                    primary_company_id="UNKNOWN",
                    observed_assignee=primary,
                    co_assignees=co_assignees,
                    status="UNKNOWN",
                )
            )
            continue
        if sources_conflict:
            needs_unknown = True
            assignments.append(
                CompanyAssignment(
                    publication_number=publication,
                    primary_company_id="UNKNOWN",
                    observed_assignee=primary,
                    co_assignees=[],
                    status="REVIEW_REQUIRED",
                )
            )
            continue

        matches: list[tuple[_CompetitorIdentity, str]] = []
        for identity in identities:
            matching_names = [
                name
                for name in identity.confirmed_names
                if _is_exact_name_match(primary, name)
            ]
            if matching_names:
                matches.append(
                    (
                        identity,
                        min(
                            matching_names,
                            key=lambda name: (
                                -len(normalize_assignee_name(name)),
                                name.casefold(),
                                name,
                            ),
                        ),
                    )
                )
        if len(matches) != 1:
            needs_unknown = True
            assignments.append(
                CompanyAssignment(
                    publication_number=publication,
                    primary_company_id="UNKNOWN",
                    observed_assignee=primary,
                    co_assignees=co_assignees,
                    status="REVIEW_REQUIRED" if matches else "UNKNOWN",
                )
            )
            continue

        identity, matched_name = matches[0]
        assignments.append(
            CompanyAssignment(
                publication_number=publication,
                primary_company_id=identity.company.company_id,
                observed_assignee=primary,
                matched_alias=matched_name,
                co_assignees=co_assignees,
                status="CONFIRMED_ALIAS",
            )
        )

    companies = [identity.company for identity in identities]
    if needs_unknown:
        companies.append(UNKNOWN_COMPANY)
    return CompanyAssignmentResult(
        companies=tuple(sorted(companies, key=_company_sort_key)),
        assignments=tuple(assignments),
    )


def _assign_normalized_names(
    hits: Sequence[MergedHit],
) -> CompanyAssignmentResult:
    observed_by_key: dict[str, set[str]] = {}
    hit_values: list[tuple[MergedHit, str | None, list[str], str | None]] = []
    for hit in hits:
        primary, sources_conflict = _source_assignee(hit)
        co_assignees: list[str] = []
        key = normalize_assignee_name(primary) if primary else None
        if key and not sources_conflict:
            observed_by_key.setdefault(key, set()).add(primary)
        hit_values.append(
            (hit, primary, co_assignees, None if sources_conflict else key)
        )

    canonical_by_key = {
        key: min(values, key=lambda value: (value.casefold(), value))
        for key, values in observed_by_key.items()
    }
    ids = _stable_company_ids(canonical_by_key.values())
    companies = [
        NormalizedCompany(
            company_id=ids[canonical],
            canonical_name=canonical,
            aliases=sorted(
                (value for value in observed_by_key[key] if value != canonical),
                key=lambda value: (value.casefold(), value),
            ),
        )
        for key, canonical in canonical_by_key.items()
    ]

    assignments: list[CompanyAssignment] = []
    needs_unknown = False
    for hit, primary, co_assignees, key in hit_values:
        publication = normalize_publication_number(hit.publication_number)
        if key is None:
            needs_unknown = True
            assignments.append(
                CompanyAssignment(
                    publication_number=publication,
                    primary_company_id="UNKNOWN",
                    observed_assignee=None,
                    co_assignees=co_assignees,
                    status="REVIEW_REQUIRED" if primary is not None else "UNKNOWN",
                )
            )
            continue
        canonical = canonical_by_key[key]
        assignments.append(
            CompanyAssignment(
                publication_number=publication,
                primary_company_id=ids[canonical],
                observed_assignee=primary,
                co_assignees=co_assignees,
                status="NORMALIZED_NAME",
            )
        )

    if needs_unknown:
        companies.append(UNKNOWN_COMPANY)
    return CompanyAssignmentResult(
        companies=tuple(sorted(companies, key=_company_sort_key)),
        assignments=tuple(assignments),
    )


def _source_assignee(hit: MergedHit) -> tuple[str | None, bool]:
    values: list[str] = []
    if hit.assignee and hit.assignee.strip():
        values.append(" ".join(hit.assignee.split()))
    for source in hit.sources:
        raw_assignee = source.raw.get("_landscape_assignee_observation")
        if raw_assignee is None:
            raw_assignee = source.raw.get("assignee")
        if isinstance(raw_assignee, str) and raw_assignee.strip():
            values.append(" ".join(raw_assignee.split()))
    unique_keys = {normalize_assignee_name(value) for value in values}
    observed = " ".join(hit.assignee.split()) if hit.assignee else None
    if observed is None and values:
        observed = min(values, key=lambda value: (value.casefold(), value))
    return observed, len(unique_keys) > 1


def _is_exact_name_match(observed: str, confirmed: str) -> bool:
    observed_key = normalize_assignee_name(observed)
    confirmed_key = normalize_assignee_name(confirmed)
    return bool(observed_key and observed_key == confirmed_key)


def _word_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(_NON_WORD.sub(" ", normalized).split())


def _stable_company_ids(names: Iterable[str]) -> dict[str, str]:
    return {
        name: f"CO-RAW-{_digest(normalize_assignee_name(name), 16)}"
        for name in names
    }


def _stable_competitor_ids(names: Iterable[str]) -> dict[str, str]:
    names = tuple(names)
    base_by_name = {
        name: _competitor_company_id_base(name)
        for name in names
    }
    collisions: dict[str, list[str]] = {}
    for name, base in base_by_name.items():
        collisions.setdefault(base, []).append(name)
    ambiguous = sorted(base for base, values in collisions.items() if len(values) > 1)
    if ambiguous:
        raise CompanyAssignmentValidationError(
            "confirmed competitor company ID collision: " + ", ".join(ambiguous)
        )
    return base_by_name


def _competitor_company_id_base(name: str) -> str:
    word_key = _word_key(name).upper()
    first_word = word_key.split(maxsplit=1)[0] if word_key else ""
    slug = _ASCII_ID_PART.sub("-", first_word).strip("-")
    if slug:
        return f"CO-{slug[:48]}"
    return f"CO-{_digest(normalize_assignee_name(name), 12)}"


def _digest(value: str, length: int) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length].upper()


def _company_sort_key(company: NormalizedCompany) -> tuple[bool, str]:
    return company.company_id == "UNKNOWN", company.company_id
