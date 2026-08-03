from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import date
from typing import Mapping, Sequence

from pydantic import ValidationError

from .scope import ScopeDraft


class ScopePersistenceError(RuntimeError):
    """Raised when persisted Scope data cannot be trusted."""


@dataclass(frozen=True)
class PreparedScopeDraftRows:
    draft: dict
    revision: dict
    companies: tuple[dict, ...]
    names: tuple[dict, ...]
    terms: tuple[dict, ...]


def prepare_scope_draft_rows(
    scope: ScopeDraft,
    *,
    created_at: int | None = None,
) -> PreparedScopeDraftRows:
    """Create a deterministic relational representation of one draft revision."""

    # Re-validate caller-created model instances before crossing the persistence
    # boundary. This also guarantees secrets/unknown fields cannot enter JSON.
    scope = ScopeDraft.model_validate(scope.model_dump(mode="json"))
    timestamp = int(time.time() * 1000) if created_at is None else created_at
    if not isinstance(timestamp, int) or isinstance(timestamp, bool) or timestamp < 0:
        raise ValueError("created_at must be a non-negative integer")

    content = scope.model_dump(mode="json")
    content_hash = _hash_json(content)
    common = {
        "draft_id": scope.draft_id,
        "revision": scope.revision,
        "status": scope.status.value,
        "mode": scope.mode.value,
        "publication_start": scope.publication_start.isoformat(),
        "publication_end": scope.publication_end.isoformat(),
        "technology_input": scope.technology_input,
        "content_hash": content_hash,
    }
    draft_row = {
        **common,
        "confirmed_scope_revision_id": None,
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    revision_row = {
        **common,
        "content_json": content,
        "created_at": timestamp,
    }

    companies: list[dict] = []
    names: list[dict] = []
    for company_order, company in enumerate(scope.companies, start=1):
        companies.append(
            {
                "draft_id": scope.draft_id,
                "draft_revision": scope.revision,
                "profile_id": company.profile_id,
                "display_name": company.display_name,
                "input_name": company.input_name,
                "sort_order": company_order,
            }
        )
        for name_order, item in enumerate(company.names, start=1):
            names.append(
                {
                    "draft_id": scope.draft_id,
                    "draft_revision": scope.revision,
                    "profile_id": company.profile_id,
                    "name_id": item.name_id,
                    "name_text": item.text,
                    "normalized_text": item.normalized_text,
                    "language": item.language.value,
                    "relation_type": item.relation_type.value,
                    "source": item.source.value,
                    "status": item.status.value,
                    "rationale": item.rationale,
                    "sort_order": name_order,
                }
            )

    terms = tuple(
        {
            "draft_id": scope.draft_id,
            "draft_revision": scope.revision,
            "term_id": item.term_id,
            "term_text": item.text,
            "normalized_text": item.normalized_text,
            "language": item.language.value,
            "relation_to_original": item.relation_to_original.value,
            "source": item.source.value,
            "status": item.status.value,
            "rationale": item.rationale,
            "sort_order": order,
        }
        for order, item in enumerate(scope.technology_terms, start=1)
    )
    return PreparedScopeDraftRows(
        draft=draft_row,
        revision=revision_row,
        companies=tuple(companies),
        names=tuple(names),
        terms=terms,
    )


def validate_persisted_scope_draft(
    draft_row: Mapping[str, object],
    revision_row: Mapping[str, object],
    company_rows: Sequence[Mapping[str, object]],
    name_rows: Sequence[Mapping[str, object]],
    term_rows: Sequence[Mapping[str, object]],
) -> ScopeDraft:
    """Rebuild a draft only when JSON and every relational projection agree."""

    raw_content = revision_row.get("content_json")
    if isinstance(raw_content, str):
        try:
            raw_content = json.loads(raw_content)
        except json.JSONDecodeError as exc:
            raise ScopePersistenceError("stored scope draft content is invalid JSON") from exc
    try:
        scope = ScopeDraft.model_validate(raw_content)
    except (ValidationError, TypeError) as exc:
        raise ScopePersistenceError("stored scope draft content failed validation") from exc

    expected = prepare_scope_draft_rows(scope, created_at=0)
    _assert_row_matches(
        "draft",
        draft_row,
        expected.draft,
        ignored={"created_at", "updated_at", "confirmed_scope_revision_id"},
    )
    _assert_row_matches(
        "revision",
        revision_row,
        expected.revision,
        ignored={"created_at", "content_json"},
    )
    _assert_rows_match("companies", company_rows, expected.companies)
    _assert_rows_match("company names", name_rows, expected.names)
    _assert_rows_match("technology terms", term_rows, expected.terms)
    return scope


def _assert_rows_match(
    label: str,
    actual_rows: Sequence[Mapping[str, object]],
    expected_rows: Sequence[Mapping[str, object]],
) -> None:
    if len(actual_rows) != len(expected_rows):
        raise ScopePersistenceError(f"stored scope {label} count mismatch")
    for index, (actual, expected) in enumerate(
        zip(actual_rows, expected_rows, strict=True), start=1
    ):
        _assert_row_matches(f"{label} row {index}", actual, expected)


def _assert_row_matches(
    label: str,
    actual: Mapping[str, object],
    expected: Mapping[str, object],
    *,
    ignored: set[str] | None = None,
) -> None:
    ignored = ignored or set()
    for key, expected_value in expected.items():
        if key in ignored:
            continue
        actual_value = actual.get(key)
        if isinstance(actual_value, date):
            actual_value = actual_value.isoformat()
        if actual_value != expected_value:
            raise ScopePersistenceError(f"stored scope {label} field mismatch: {key}")


def _hash_json(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = [
    "PreparedScopeDraftRows",
    "ScopePersistenceError",
    "prepare_scope_draft_rows",
    "validate_persisted_scope_draft",
]
