#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from idea.rag_eval import (  # noqa: E402
    RagEvalDataset,
    RagEvalGates,
    RagEvalRun,
    RagEvalSummary,
    ObservedHit,
    collect_rag_run,
    compare_rag_summaries,
    evaluate_rag_run,
)
from idea.lexical import LexicalSearchRequest  # noqa: E402
from idea.postgres_lexical import PostgreSQLLexicalSearchRepository  # noqa: E402


def _read(path: Path, model):
    return model.model_validate_json(path.read_text(encoding="utf-8"))


def _write_or_print(value, output: Path | None) -> None:
    payload = value.model_dump_json(indent=2)
    if output is None:
        print(payload)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(payload + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(output),
        "passed": getattr(value, "passed", True),
    }))


def _evaluate(args: argparse.Namespace) -> int:
    dataset = _read(args.dataset, RagEvalDataset)
    run = _read(args.run, RagEvalRun)
    summary = evaluate_rag_run(
        dataset,
        run,
        cutoff_k=args.cutoff,
        gates=RagEvalGates(
            min_macro_recall_at_k=args.min_recall,
            min_macro_ndcg_at_k=args.min_ndcg,
            min_macro_citation_recall=args.min_citation_recall,
            max_scope_violations=args.max_scope_violations,
            max_citation_violations=args.max_citation_violations,
        ),
    )
    _write_or_print(summary, args.output)
    return 0 if summary.passed else 2


def _compare(args: argparse.Namespace) -> int:
    baseline = _read(args.baseline, RagEvalSummary)
    candidate = _read(args.candidate, RagEvalSummary)
    result = compare_rag_summaries(
        baseline,
        candidate,
        min_recall_delta=args.min_recall_delta,
        min_ndcg_delta=args.min_ndcg_delta,
        min_citation_recall_delta=args.min_citation_recall_delta,
    )
    _write_or_print(result, args.output)
    return 0 if result.passed else 3


async def _collect_postgres_lexical_async(args: argparse.Namespace) -> RagEvalRun:
    dataset = _read(args.dataset, RagEvalDataset)
    repository = PostgreSQLLexicalSearchRepository(args.dsn)

    async def search(case, limit):
        digest = hashlib.sha256(
            f"{dataset.dataset_id}|{case.case_id}|{case.query_text}".encode("utf-8")
        ).hexdigest()[:24]
        hits = await repository.search(LexicalSearchRequest(
            query_id=f"EV-{digest}",
            text=case.query_text,
            allowed_version_ids=case.allowed_version_ids,
            limit=limit,
        ))
        return tuple(
            ObservedHit(
                chunk_id=item.chunk.chunk_id,
                version_id=item.chunk.version_id,
                rank=item.rank,
                score=item.lexical_score,
                sources=(f"lexical:{item.match_kind}",),
            )
            for item in hits
        )

    return await collect_rag_run(
        dataset,
        search,
        run_id=args.run_id,
        system_version=args.system_version,
        retriever_version=args.retriever_version,
        limit=args.limit,
    )


def _collect_postgres_lexical(args: argparse.Namespace) -> int:
    result = asyncio.run(_collect_postgres_lexical_async(args))
    _write_or_print(result, args.output)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate initial-report and follow-up RAG with frozen Chunk judgments."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    evaluate = commands.add_parser("evaluate", help="score one observed retrieval run")
    evaluate.add_argument("--dataset", type=Path, required=True)
    evaluate.add_argument("--run", type=Path, required=True)
    evaluate.add_argument("--output", type=Path)
    evaluate.add_argument("--cutoff", type=int, default=10)
    evaluate.add_argument("--min-recall", type=float, default=0.0)
    evaluate.add_argument("--min-ndcg", type=float, default=0.0)
    evaluate.add_argument("--min-citation-recall", type=float, default=0.0)
    evaluate.add_argument("--max-scope-violations", type=int, default=0)
    evaluate.add_argument("--max-citation-violations", type=int, default=0)
    evaluate.set_defaults(handler=_evaluate)

    compare = commands.add_parser("compare", help="compare candidate summary to baseline")
    compare.add_argument("--baseline", type=Path, required=True)
    compare.add_argument("--candidate", type=Path, required=True)
    compare.add_argument("--output", type=Path)
    compare.add_argument("--min-recall-delta", type=float, default=0.0)
    compare.add_argument("--min-ndcg-delta", type=float, default=0.0)
    compare.add_argument("--min-citation-recall-delta", type=float, default=0.0)
    compare.set_defaults(handler=_compare)

    collect = commands.add_parser(
        "collect-postgres-lexical",
        help="run every frozen evaluation query against PostgreSQL lexical search",
    )
    collect.add_argument("--dataset", type=Path, required=True)
    collect.add_argument("--output", type=Path, required=True)
    collect.add_argument("--run-id", required=True)
    collect.add_argument("--system-version", required=True)
    collect.add_argument("--retriever-version", default="postgres-lexical-eval-v1")
    collect.add_argument("--limit", type=int, default=10)
    collect.add_argument(
        "--dsn",
        help="PostgreSQL DSN; omitted value uses AIFPATENT_POSTGRES_DSN",
    )
    collect.set_defaults(handler=_collect_postgres_lexical)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
