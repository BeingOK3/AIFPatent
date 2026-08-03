from __future__ import annotations

import hashlib
import json
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from typing import Callable, Iterator, Mapping, Sequence

from pydantic import ValidationError

from .scope import (
    CandidateStatus,
    CompanyScopeDraft,
    ConfirmedScopeRevision,
    ScopeDraft,
    normalize_scope_text,
)


class ScopePersistenceError(RuntimeError):
    """Raised when persisted Scope data cannot be trusted."""


class ScopeRevisionConflict(ScopePersistenceError):
    """Raised when a caller attempts to overwrite a newer draft revision."""


@dataclass(frozen=True)
class PreparedScopeDraftRows:
    draft: dict
    revision: dict
    companies: tuple[dict, ...]
    names: tuple[dict, ...]
    terms: tuple[dict, ...]


@dataclass(frozen=True)
class PreparedCompanyProfileRows:
    profile: dict
    version: dict
    names: tuple[dict, ...]


@dataclass(frozen=True)
class PreparedConfirmedScopeRows:
    revision: dict
    companies: tuple[dict, ...]
    names: tuple[dict, ...]
    terms: tuple[dict, ...]


class PostgreSQLScopeDraftRepository:
    """Transactional PostgreSQL repository for reviewable Scope drafts."""

    def __init__(self, dsn: str, *, connect: Callable[[], object] | None = None):
        if not isinstance(dsn, str) or not dsn.strip():
            raise ValueError("PostgreSQL DSN must not be blank")
        self._dsn = dsn.strip()
        self._connect_factory = connect

    def create(self, scope: ScopeDraft) -> ScopeDraft:
        if scope.revision != 1:
            raise ScopePersistenceError("a new scope draft must start at revision 1")
        rows = prepare_scope_draft_rows(scope)
        with self._connect() as connection:
            inserted = connection.execute(
                """
                INSERT INTO landscape_v4_scope_drafts(
                    draft_id,revision,status,mode,publication_start,publication_end,
                    technology_input,content_hash,confirmed_scope_revision_id,
                    created_at,updated_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (draft_id) DO NOTHING
                RETURNING draft_id
                """,
                _values(
                    rows.draft,
                    "draft_id", "revision", "status", "mode",
                    "publication_start", "publication_end", "technology_input",
                    "content_hash", "confirmed_scope_revision_id", "created_at",
                    "updated_at",
                ),
            ).fetchone()
            if inserted is None:
                existing = self._load(connection, scope.draft_id)
                if existing != scope:
                    raise ScopeRevisionConflict(
                        "scope draft ID already exists with different content"
                    )
                return existing
            self._insert_revision(connection, rows)
            return self._load(connection, scope.draft_id)

    def get(self, draft_id: str) -> ScopeDraft:
        if not isinstance(draft_id, str) or not draft_id.strip():
            raise ValueError("draft_id must not be blank")
        with self._connect() as connection:
            return self._load(connection, draft_id.strip())

    def update(self, scope: ScopeDraft, *, expected_revision: int) -> ScopeDraft:
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool):
            raise ValueError("expected_revision must be an integer")
        if expected_revision < 1 or scope.revision != expected_revision + 1:
            raise ScopeRevisionConflict(
                "updated scope revision must be exactly expected_revision + 1"
            )
        rows = prepare_scope_draft_rows(scope)
        with self._connect() as connection:
            current = connection.execute(
                """
                SELECT draft_id,revision,status
                FROM landscape_v4_scope_drafts
                WHERE draft_id=%s
                FOR UPDATE
                """,
                (scope.draft_id,),
            ).fetchone()
            if current is None:
                raise KeyError(scope.draft_id)
            if current["revision"] != expected_revision:
                raise ScopeRevisionConflict(
                    f"scope draft revision changed: expected {expected_revision}, "
                    f"found {current['revision']}"
                )
            if current["status"] == "CONFIRMED" or scope.status.value == "CONFIRMED":
                raise ScopeRevisionConflict(
                    "confirmed scope drafts cannot be changed through draft update"
                )

            changed = connection.execute(
                """
                UPDATE landscape_v4_scope_drafts
                SET revision=%s,status=%s,mode=%s,publication_start=%s,
                    publication_end=%s,technology_input=%s,content_hash=%s,
                    updated_at=%s
                WHERE draft_id=%s AND revision=%s AND status<>'CONFIRMED'
                """,
                (
                    rows.draft["revision"], rows.draft["status"], rows.draft["mode"],
                    rows.draft["publication_start"], rows.draft["publication_end"],
                    rows.draft["technology_input"], rows.draft["content_hash"],
                    rows.draft["updated_at"], scope.draft_id, expected_revision,
                ),
            )
            if changed.rowcount != 1:
                raise ScopeRevisionConflict("scope draft compare-and-swap failed")
            self._insert_revision(connection, rows)
            return self._load(connection, scope.draft_id)

    def _insert_revision(self, connection: object, rows: PreparedScopeDraftRows) -> None:
        revision = rows.revision
        connection.execute(
            """
            INSERT INTO landscape_v4_scope_draft_revisions(
                draft_id,revision,status,mode,publication_start,publication_end,
                technology_input,content_json,content_hash,created_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)
            """,
            (
                revision["draft_id"], revision["revision"], revision["status"],
                revision["mode"], revision["publication_start"],
                revision["publication_end"], revision["technology_input"],
                _canonical_json(revision["content_json"]), revision["content_hash"],
                revision["created_at"],
            ),
        )
        self._executemany(
            connection,
            """
            INSERT INTO landscape_v4_scope_draft_companies(
                draft_id,draft_revision,profile_id,display_name,input_name,sort_order
            ) VALUES (%s,%s,%s,%s,%s,%s)
            """,
            rows.companies,
            ("draft_id", "draft_revision", "profile_id", "display_name", "input_name", "sort_order"),
        )
        self._executemany(
            connection,
            """
            INSERT INTO landscape_v4_scope_draft_names(
                draft_id,draft_revision,profile_id,name_id,name_text,normalized_text,
                language,relation_type,source,status,rationale,sort_order
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            rows.names,
            (
                "draft_id", "draft_revision", "profile_id", "name_id", "name_text",
                "normalized_text", "language", "relation_type", "source", "status",
                "rationale", "sort_order",
            ),
        )
        self._executemany(
            connection,
            """
            INSERT INTO landscape_v4_scope_draft_terms(
                draft_id,draft_revision,term_id,term_text,normalized_text,language,
                relation_to_original,source,status,rationale,sort_order
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            rows.terms,
            (
                "draft_id", "draft_revision", "term_id", "term_text",
                "normalized_text", "language", "relation_to_original", "source",
                "status", "rationale", "sort_order",
            ),
        )

    @staticmethod
    def _executemany(
        connection: object,
        sql: str,
        rows: Sequence[Mapping[str, object]],
        keys: tuple[str, ...],
    ) -> None:
        if not rows:
            return
        with connection.cursor() as cursor:
            cursor.executemany(sql, [_values(row, *keys) for row in rows])

    @staticmethod
    def _load(connection: object, draft_id: str) -> ScopeDraft:
        draft_row = connection.execute(
            """
            SELECT draft_id,revision,status,mode,publication_start,publication_end,
                   technology_input,content_hash,confirmed_scope_revision_id,
                   created_at,updated_at
            FROM landscape_v4_scope_drafts
            WHERE draft_id=%s
            """,
            (draft_id,),
        ).fetchone()
        if draft_row is None:
            raise KeyError(draft_id)
        revision = draft_row["revision"]
        revision_row = connection.execute(
            """
            SELECT draft_id,revision,status,mode,publication_start,publication_end,
                   technology_input,content_json,content_hash,created_at
            FROM landscape_v4_scope_draft_revisions
            WHERE draft_id=%s AND revision=%s
            """,
            (draft_id, revision),
        ).fetchone()
        if revision_row is None:
            raise ScopePersistenceError("current scope draft revision is missing")
        company_rows = connection.execute(
            """
            SELECT draft_id,draft_revision,profile_id,display_name,input_name,sort_order
            FROM landscape_v4_scope_draft_companies
            WHERE draft_id=%s AND draft_revision=%s
            ORDER BY sort_order
            """,
            (draft_id, revision),
        ).fetchall()
        name_rows = connection.execute(
            """
            SELECT n.draft_id,n.draft_revision,n.profile_id,n.name_id,n.name_text,
                   n.normalized_text,n.language,n.relation_type,n.source,n.status,
                   n.rationale,n.sort_order
            FROM landscape_v4_scope_draft_names n
            JOIN landscape_v4_scope_draft_companies c
              ON c.draft_id=n.draft_id AND c.draft_revision=n.draft_revision
             AND c.profile_id=n.profile_id
            WHERE n.draft_id=%s AND n.draft_revision=%s
            ORDER BY c.sort_order,n.sort_order
            """,
            (draft_id, revision),
        ).fetchall()
        term_rows = connection.execute(
            """
            SELECT draft_id,draft_revision,term_id,term_text,normalized_text,language,
                   relation_to_original,source,status,rationale,sort_order
            FROM landscape_v4_scope_draft_terms
            WHERE draft_id=%s AND draft_revision=%s
            ORDER BY sort_order
            """,
            (draft_id, revision),
        ).fetchall()
        return validate_persisted_scope_draft(
            draft_row, revision_row, company_rows, name_rows, term_rows
        )

    @contextmanager
    def _connect(self) -> Iterator[object]:
        if self._connect_factory is not None:
            with self._connect_factory() as connection:
                yield connection
            return
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise ScopePersistenceError(
                "psycopg is required for Scope persistence"
            ) from exc
        with psycopg.connect(
            self._dsn, row_factory=dict_row, connect_timeout=10
        ) as connection:
            yield connection


def prepare_scope_draft_rows(
    scope: ScopeDraft,
    *,
    created_at: int | None = None,
) -> PreparedScopeDraftRows:
    """Create a deterministic relational representation of one draft revision."""

    # Re-validate caller-created model instances before crossing the persistence
    # boundary. This also guarantees secrets/unknown fields cannot enter JSON.
    scope = ScopeDraft.model_validate(scope.model_dump(mode="json"))
    timestamp = _timestamp(created_at)

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


def prepare_company_profile_rows(
    company: CompanyScopeDraft,
    *,
    profile_version: int,
    confirmed_scope_revision_id: str,
    created_at: int | None = None,
) -> PreparedCompanyProfileRows:
    company = CompanyScopeDraft.model_validate(company.model_dump(mode="json"))
    if not isinstance(profile_version, int) or isinstance(profile_version, bool) or profile_version < 1:
        raise ValueError("profile_version must be a positive integer")
    if not isinstance(confirmed_scope_revision_id, str) or not confirmed_scope_revision_id.startswith("SCR-"):
        raise ValueError("confirmed_scope_revision_id must be an SCR identifier")
    unresolved = [item.name_id for item in company.names if item.status == CandidateStatus.PROPOSED]
    if unresolved:
        raise ScopePersistenceError(
            "company profile cannot persist unresolved proposed names"
        )
    timestamp = _timestamp(created_at)
    snapshot = {
        "profile_id": company.profile_id,
        "display_name": company.display_name,
        "names": [item.model_dump(mode="json") for item in company.names],
    }
    snapshot_hash = _hash_json(snapshot)
    profile = {
        "profile_id": company.profile_id,
        "anchor_normalized": normalize_scope_text(company.input_name),
        "display_name": company.display_name,
        "current_version": profile_version,
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    version = {
        "profile_id": company.profile_id,
        "version": profile_version,
        "snapshot_hash": snapshot_hash,
        "confirmed_scope_revision_id": confirmed_scope_revision_id,
        "created_at": timestamp,
    }
    names = tuple(
        {
            "profile_id": company.profile_id,
            "profile_version": profile_version,
            "name_id": item.name_id,
            "name_text": item.text,
            "normalized_text": item.normalized_text,
            "language": item.language.value,
            "relation_type": item.relation_type.value,
            "source": item.source.value,
            "status": item.status.value,
            "rationale": item.rationale,
            "created_at": timestamp,
        }
        for item in company.names
    )
    return PreparedCompanyProfileRows(
        profile=profile,
        version=version,
        names=names,
    )


def prepare_confirmed_scope_rows(
    scope: ConfirmedScopeRevision,
    *,
    created_at: int | None = None,
) -> PreparedConfirmedScopeRows:
    scope = ConfirmedScopeRevision.model_validate(scope.model_dump(mode="json"))
    timestamp = _timestamp(created_at)
    snapshot = scope.model_dump(mode="json")
    if _hash_confirmed_scope_snapshot(snapshot) != scope.scope_revision_hash:
        raise ScopePersistenceError("confirmed scope revision hash is invalid")
    revision = {
        "scope_revision_id": scope.scope_revision_id,
        "scope_revision_hash": scope.scope_revision_hash,
        "source_draft_id": scope.source_draft_id,
        "source_draft_revision": scope.source_draft_revision,
        "mode": scope.mode.value,
        "publication_start": scope.publication_start.isoformat(),
        "publication_end": scope.publication_end.isoformat(),
        "technology_input": scope.technology_input,
        "snapshot_json": snapshot,
        "created_at": timestamp,
    }
    companies: list[dict] = []
    names: list[dict] = []
    for company_order, company in enumerate(scope.companies, start=1):
        companies.append(
            {
                "scope_revision_id": scope.scope_revision_id,
                "profile_id": company.profile_id,
                "profile_version": company.profile_version,
                "display_name": company.display_name,
                "sort_order": company_order,
            }
        )
        for name_order, item in enumerate(company.names, start=1):
            names.append(
                {
                    "scope_revision_id": scope.scope_revision_id,
                    "profile_id": company.profile_id,
                    "name_id": item.name_id,
                    "name_text": item.text,
                    "normalized_text": item.normalized_text,
                    "language": item.language.value,
                    "relation_type": item.relation_type.value,
                    "sort_order": name_order,
                }
            )
    terms = tuple(
        {
            "scope_revision_id": scope.scope_revision_id,
            "term_id": item.term_id,
            "term_text": item.text,
            "normalized_text": item.normalized_text,
            "language": item.language.value,
            "relation_to_original": item.relation_to_original.value,
            "sort_order": order,
        }
        for order, item in enumerate(scope.technology_terms, start=1)
    )
    return PreparedConfirmedScopeRows(
        revision=revision,
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


def validate_persisted_confirmed_scope(
    revision_row: Mapping[str, object],
    company_rows: Sequence[Mapping[str, object]],
    name_rows: Sequence[Mapping[str, object]],
    term_rows: Sequence[Mapping[str, object]],
) -> ConfirmedScopeRevision:
    raw_snapshot = revision_row.get("snapshot_json")
    if isinstance(raw_snapshot, str):
        try:
            raw_snapshot = json.loads(raw_snapshot)
        except json.JSONDecodeError as exc:
            raise ScopePersistenceError("stored confirmed scope is invalid JSON") from exc
    try:
        scope = ConfirmedScopeRevision.model_validate(raw_snapshot)
    except (ValidationError, TypeError) as exc:
        raise ScopePersistenceError("stored confirmed scope failed validation") from exc
    expected = prepare_confirmed_scope_rows(scope, created_at=0)
    _assert_row_matches(
        "confirmed revision",
        revision_row,
        expected.revision,
        ignored={"created_at", "snapshot_json"},
    )
    _assert_rows_match("confirmed companies", company_rows, expected.companies)
    _assert_rows_match("confirmed company names", name_rows, expected.names)
    _assert_rows_match("confirmed technology terms", term_rows, expected.terms)
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
    encoded = _canonical_json(value)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _hash_confirmed_scope_snapshot(snapshot: Mapping[str, object]) -> str:
    semantic = dict(snapshot)
    scope_revision_id = semantic.pop("scope_revision_id", None)
    scope_revision_hash = semantic.pop("scope_revision_hash", None)
    calculated = _hash_json(semantic)
    if scope_revision_hash != calculated or scope_revision_id != f"SCR-{calculated[:16]}":
        return ""
    return calculated


def _timestamp(value: int | None) -> int:
    timestamp = int(time.time() * 1000) if value is None else value
    if not isinstance(timestamp, int) or isinstance(timestamp, bool) or timestamp < 0:
        raise ValueError("created_at must be a non-negative integer")
    return timestamp


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _values(row: Mapping[str, object], *keys: str) -> tuple[object, ...]:
    return tuple(row[key] for key in keys)


__all__ = [
    "PreparedScopeDraftRows",
    "PreparedCompanyProfileRows",
    "PreparedConfirmedScopeRows",
    "PostgreSQLScopeDraftRepository",
    "ScopePersistenceError",
    "ScopeRevisionConflict",
    "prepare_scope_draft_rows",
    "prepare_company_profile_rows",
    "prepare_confirmed_scope_rows",
    "validate_persisted_confirmed_scope",
    "validate_persisted_scope_draft",
]
