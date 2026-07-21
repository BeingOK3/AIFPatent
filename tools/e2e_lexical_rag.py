#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools import idea_workflow


class E2EValidationError(RuntimeError):
    pass


def validate_report(report: dict) -> dict[str, object]:
    if report.get("schema_version") != "2.0":
        raise E2EValidationError("expected report schema_version 2.0")
    citations = report.get("citations")
    if not isinstance(citations, list):
        raise E2EValidationError("report citations must be a list")
    provenance = report.get("rag_provenance")
    if not isinstance(provenance, dict):
        raise E2EValidationError("report has no RAG provenance")
    provenance_fields = (
        "context_ids", "corpus_snapshot_hashes", "prompt_versions",
        "retriever_versions", "context_hashes", "citation_text_hashes",
    )
    for field in provenance_fields:
        if not isinstance(provenance.get(field), list):
            raise E2EValidationError(f"RAG provenance {field} must be a list")
    if not provenance["context_ids"] or not provenance["context_hashes"]:
        raise E2EValidationError("RAG provenance has no Contexts")
    required = {
        "context_id", "feature_id", "alias", "chunk_id", "version_id",
        "publication_number", "section_type", "section_label", "start_offset",
        "end_offset", "text_hash", "excerpt", "corpus_snapshot_hash",
        "prompt_version", "retriever_version", "context_hash",
    }
    for citation in citations:
        if not isinstance(citation, dict) or required - citation.keys():
            raise E2EValidationError("citation contract is incomplete")
        excerpt = citation["excerpt"]
        if hashlib.sha256(excerpt.encode("utf-8")).hexdigest() != citation["text_hash"]:
            raise E2EValidationError("citation excerpt hash mismatch")
        if not citation["alias"].startswith("C") or not citation["context_id"].startswith("CTX-"):
            raise E2EValidationError("citation alias/context identity is invalid")
    mapped = []
    for document in report.get("deep_review_documents", []):
        for mapping in document.get("feature_mappings", []):
            mapping_citations = mapping.get("citations", [])
            status = mapping.get("status")
            if status in {"DISCLOSED", "PARTIAL"} and not mapping_citations:
                raise E2EValidationError("positive feature mapping has no Citation")
            if status in {"NOT_DISCLOSED", "UNCERTAIN"} and mapping_citations:
                raise E2EValidationError("negative feature mapping contains a Citation")
            mapped.extend(mapping_citations)
    if len(mapped) != len(citations):
        raise E2EValidationError("report Citation list differs from mapped Citations")
    if set(provenance["citation_text_hashes"]) != {
        item["text_hash"] for item in citations
    }:
        raise E2EValidationError("RAG provenance Citation hashes are incomplete")
    return {
        "ok": True,
        "run_id": report.get("run_id"),
        "schema_version": report["schema_version"],
        "citation_count": len(citations),
        "context_count": len(provenance["context_ids"]),
        "publication_count": len({item["publication_number"] for item in citations}),
    }


def _read_environment(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise E2EValidationError(f"PostgreSQL environment file is missing: {path}")
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name.strip()] = value.strip()
    return values


def postgres_dsn_from_env(path: Path) -> str:
    values = _read_environment(path)
    required = (
        "AIFPATENT_POSTGRES_USER",
        "AIFPATENT_POSTGRES_PASSWORD",
        "AIFPATENT_POSTGRES_DB",
        "AIFPATENT_POSTGRES_PORT",
    )
    missing = [name for name in required if not values.get(name)]
    if missing:
        raise E2EValidationError(
            "PostgreSQL environment is incomplete: " + ", ".join(missing)
        )
    return (
        "postgresql://"
        f"{quote(values['AIFPATENT_POSTGRES_USER'], safe='')}:"
        f"{quote(values['AIFPATENT_POSTGRES_PASSWORD'], safe='')}"
        f"@127.0.0.1:{values['AIFPATENT_POSTGRES_PORT']}/"
        f"{quote(values['AIFPATENT_POSTGRES_DB'], safe='')}"
    )


def _verify_source_row(citation: dict[str, Any], row: tuple[Any, ...]) -> None:
    fields = (
        "chunk_id", "version_id", "publication_number", "section_type",
        "section_label", "claim_number", "start_offset", "end_offset", "text",
        "text_hash", "corpus_availability", "deep_reviewed", "version_state",
    )
    if len(row) != len(fields):
        raise E2EValidationError("PostgreSQL Citation source row is incomplete")
    source = dict(zip(fields, row))
    source["claim_number"] = (
        None if source["claim_number"] is None else int(source["claim_number"])
    )
    for field in (
        "chunk_id", "version_id", "publication_number", "section_type",
        "section_label", "claim_number", "start_offset", "end_offset", "text_hash",
    ):
        if citation.get(field) != source[field]:
            raise E2EValidationError(f"PostgreSQL source mismatch for Citation {field}")
    if citation.get("excerpt") != source["text"]:
        raise E2EValidationError("PostgreSQL source text does not match Citation excerpt")
    if (
        source["corpus_availability"] != "READY"
        or source["deep_reviewed"] is not True
        or source["version_state"] != "READY"
    ):
        raise E2EValidationError("Citation source is not a READY deep-reviewed Run Version")


def validate_postgres_sources(
    report: dict[str, Any],
    dsn: str,
    *,
    connect: Callable[[str], Any] | None = None,
) -> dict[str, int]:
    try:
        if connect is None:
            import psycopg
            connection = psycopg.connect(dsn)
        else:
            connection = connect(dsn)
    except Exception as exc:
        raise E2EValidationError(
            "cannot connect to PostgreSQL for Citation verification"
        ) from exc
    run_id = str(report.get("run_id") or "")
    verified = 0
    verified_contexts = 0
    verified_versions: set[str] = set()
    provenance = report["rag_provenance"]
    actual_provenance = {
        "context_ids": set(),
        "corpus_snapshot_hashes": set(),
        "prompt_versions": set(),
        "retriever_versions": set(),
        "context_hashes": set(),
    }
    try:
        with connection.cursor() as cursor:
            for context_id in provenance["context_ids"]:
                cursor.execute(
                    """
                    SELECT context_id, corpus_snapshot_hash, prompt_version,
                           retriever_version, context_hash, allowed_version_ids_json
                    FROM model_context_manifests
                    WHERE run_id = %s AND purpose = 'INITIAL_REVIEW' AND context_id = %s
                    """,
                    (run_id, context_id),
                )
                row = cursor.fetchone()
                if row is None:
                    raise E2EValidationError("report Context is absent from PostgreSQL")
                (
                    stored_context_id, snapshot_hash, prompt_version,
                    retriever_version, context_hash, allowed_versions,
                ) = row
                if isinstance(allowed_versions, str):
                    allowed_versions = json.loads(allowed_versions)
                if not isinstance(allowed_versions, list) or not allowed_versions:
                    raise E2EValidationError("Context has no frozen allowed Versions")
                actual_provenance["context_ids"].add(str(stored_context_id))
                actual_provenance["corpus_snapshot_hashes"].add(str(snapshot_hash))
                actual_provenance["prompt_versions"].add(str(prompt_version))
                actual_provenance["retriever_versions"].add(str(retriever_version))
                actual_provenance["context_hashes"].add(str(context_hash))
                for version_id in allowed_versions:
                    cursor.execute(
                        """
                        SELECT rdv.corpus_availability, rdv.deep_reviewed, pv.state
                        FROM run_document_versions AS rdv
                        JOIN patent_document_versions AS pv
                          ON pv.version_id = rdv.version_id
                        WHERE rdv.run_id = %s AND rdv.version_id = %s
                        """,
                        (run_id, version_id),
                    )
                    readiness = cursor.fetchone()
                    if readiness is None or tuple(readiness) != ("READY", True, "READY"):
                        raise E2EValidationError(
                            "Context Version is not READY and deep-reviewed in its Run scope"
                        )
                    verified_versions.add(str(version_id))
                verified_contexts += 1
            for field, actual in actual_provenance.items():
                if actual != {str(item) for item in provenance[field]}:
                    raise E2EValidationError(
                        f"PostgreSQL Context provenance mismatch for {field}"
                    )
            for citation in report.get("citations", []):
                cursor.execute(
                    """
                    SELECT c.chunk_id, c.version_id, c.publication_number,
                           c.section_type, c.section_label, c.claim_number,
                           c.start_offset, c.end_offset, c.text, c.text_hash,
                           rdv.corpus_availability, rdv.deep_reviewed,
                           pv.state AS version_state
                    FROM patent_chunks AS c
                    JOIN run_document_versions AS rdv
                      ON rdv.version_id = c.version_id
                    JOIN patent_document_versions AS pv
                      ON pv.version_id = c.version_id
                    WHERE rdv.run_id = %s AND c.chunk_id = %s AND c.version_id = %s
                    """,
                    (run_id, citation["chunk_id"], citation["version_id"]),
                )
                source = cursor.fetchone()
                if source is None:
                    raise E2EValidationError(
                        "Citation Chunk is absent from its Run Version scope"
                    )
                _verify_source_row(citation, tuple(source))
                cursor.execute(
                    """
                    SELECT m.corpus_snapshot_hash, m.prompt_version,
                           m.retriever_version, m.context_hash
                    FROM report_model_citations AS mc
                    JOIN model_context_manifests AS m
                      ON m.context_id = mc.context_id AND m.run_id = mc.run_id
                    WHERE mc.run_id = %s AND mc.feature_id = %s
                      AND mc.context_id = %s AND mc.alias = %s AND mc.chunk_id = %s
                    """,
                    (
                        run_id, f"{run_id}:{citation['feature_id']}",
                        citation["context_id"], citation["alias"], citation["chunk_id"],
                    ),
                )
                provenance = cursor.fetchone()
                if provenance is None:
                    raise E2EValidationError(
                        "Citation was not selected by the document-analysis model"
                    )
                for field, value in zip(
                    (
                        "corpus_snapshot_hash", "prompt_version",
                        "retriever_version", "context_hash",
                    ),
                    provenance,
                ):
                    if citation.get(field) != value:
                        raise E2EValidationError(
                            f"PostgreSQL Context provenance mismatch for Citation {field}"
                        )
                verified += 1
    finally:
        connection.close()
    return {
        "postgres_verified_citation_count": verified,
        "postgres_verified_context_count": verified_contexts,
        "postgres_verified_version_count": len(verified_versions),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run and verify the real LEXICAL_RAG report path")
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--model-base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-key-env", default="LLM_API_KEY")
    parser.add_argument("--idea", required=True)
    parser.add_argument("--timeout", type=float, default=3600)
    parser.add_argument(
        "--postgres-env-file",
        type=Path,
        default=PROJECT_ROOT / "deploy" / "rag" / "rag.env",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if not os.environ.get(args.api_key_env, "").strip():
        print(json.dumps({"ok": False, "error": "runtime API key is missing"}), file=sys.stderr)
        return 3
    workflow_args = argparse.Namespace(
        base_url=args.base_url,
        model_base_url=args.model_base_url,
        model=args.model,
        api_key_env=args.api_key_env,
        case_id=None,
        case_title="LEXICAL_RAG E2E " + datetime.now(timezone.utc).isoformat(),
        idea=args.idea,
        idea_file=None,
        evaluation_date=date.today().isoformat(),
        date_basis="E2E 当前日期",
        mode="quick",
        candidate_max=30,
        deep_min=10,
        deep_max=10,
        timeout=args.timeout,
        poll=2.0,
    )
    try:
        created = idea_workflow.start(workflow_args)
        terminal = idea_workflow.wait_for_run(workflow_args, created["run_id"])
        if terminal.get("status") not in {"COMPLETED", "COMPLETED_WITH_LIMITATIONS"}:
            raise E2EValidationError(
                f"Run {created['run_id']} ended as {terminal.get('status')}: "
                f"{terminal.get('error_code')}"
            )
        report = idea_workflow.request(
            args.base_url, f"/api/idea/runs/{created['run_id']}/report"
        )
        summary = validate_report(report)
        summary.update(
            validate_postgres_sources(
                report, postgres_dsn_from_env(args.postgres_env_file)
            )
        )
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0
    except (E2EValidationError, idea_workflow.WorkflowClientError, KeyError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
