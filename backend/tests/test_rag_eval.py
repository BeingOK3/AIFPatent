from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from idea.rag_eval import (
    RagEvalDataset,
    RagEvalError,
    RagEvalGates,
    RagEvalRun,
    ObservedHit,
    collect_rag_run,
    compare_rag_summaries,
    evaluate_rag_run,
)
from tools.rag_eval import main


HASH_A = "a" * 64
HASH_B = "b" * 64


def dataset() -> RagEvalDataset:
    return RagEvalDataset.model_validate({
        "schema_version": "rag-eval-dataset-v1",
        "dataset_id": "shared-seed-v1",
        "judgment_policy": "Human-verified exact Chunk relevance and quote hashes.",
        "cases": [
            {
                "case_id": "report-f1-v1",
                "workflow": "INITIAL_REPORT",
                "query_text": "cache eviction by access heat",
                "allowed_version_ids": ["cv-1"],
                "relevant_chunks": [
                    {"chunk_id": "c1", "version_id": "cv-1", "relevance": 3},
                    {"chunk_id": "c2", "version_id": "cv-1", "relevance": 1},
                ],
                "required_version_ids": ["cv-1"],
                "citation_targets": [
                    {"chunk_id": "c1", "version_id": "cv-1", "quote_hash": HASH_A}
                ],
            },
            {
                "case_id": "followup-compare-v1",
                "workflow": "FOLLOWUP",
                "query_text": "compare access heat mechanisms",
                "allowed_version_ids": ["cv-1", "cv-2"],
                "relevant_chunks": [
                    {"chunk_id": "c1", "version_id": "cv-1", "relevance": 2},
                    {"chunk_id": "c3", "version_id": "cv-2", "relevance": 3},
                ],
                "required_version_ids": ["cv-1", "cv-2"],
                "citation_targets": [
                    {"chunk_id": "c3", "version_id": "cv-2", "quote_hash": HASH_B}
                ],
            },
        ],
    })


def observed_run(*, improved: bool = False) -> RagEvalRun:
    second_hits = [
        {"chunk_id": "c3", "version_id": "cv-2", "rank": 1, "sources": ["lexical"]},
        {"chunk_id": "noise", "version_id": "cv-1", "rank": 2},
    ]
    if improved:
        second_hits[1] = {"chunk_id": "c1", "version_id": "cv-1", "rank": 2}
    return RagEvalRun.model_validate({
        "schema_version": "rag-eval-run-v1",
        "run_id": "hybrid" if improved else "lexical",
        "dataset_id": "shared-seed-v1",
        "system_version": "test-sha",
        "retriever_version": "hybrid-v1" if improved else "lexical-v1",
        "observations": [
            {
                "case_id": "report-f1-v1",
                "hits": [
                    {"chunk_id": "c1", "version_id": "cv-1", "rank": 1},
                    {"chunk_id": "noise", "version_id": "cv-1", "rank": 2},
                ],
                "citations": [
                    {"chunk_id": "c1", "version_id": "cv-1", "quote_hash": HASH_A}
                ],
            },
            {
                "case_id": "followup-compare-v1",
                "hits": second_hits,
                "citations": [
                    {"chunk_id": "c3", "version_id": "cv-2", "quote_hash": HASH_B}
                ],
            },
        ],
    })


class RagEvalTests(unittest.TestCase):
    def test_dataset_rejects_relevance_outside_frozen_scope(self) -> None:
        value = dataset().model_dump(mode="json")
        value["cases"][0]["relevant_chunks"][0]["version_id"] = "cv-outside"
        with self.assertRaisesRegex(ValidationError, "escaped"):
            RagEvalDataset.model_validate(value)

    def test_metrics_cover_both_workflows_and_exact_citations(self) -> None:
        result = evaluate_rag_run(dataset(), observed_run(), cutoff_k=2)
        self.assertEqual(result.workflow_case_counts, {
            "INITIAL_REPORT": 1, "FOLLOWUP": 1,
        })
        self.assertAlmostEqual(result.macro_recall_at_k, 0.5)
        self.assertAlmostEqual(result.macro_precision_at_k, 0.5)
        self.assertEqual(result.macro_reciprocal_rank_at_k, 1.0)
        self.assertEqual(result.macro_citation_precision, 1.0)
        self.assertEqual(result.macro_citation_recall, 1.0)
        self.assertEqual(result.scope_violation_count, 0)
        self.assertTrue(result.passed)

    def test_scope_escape_and_ungrounded_citation_fail_gates(self) -> None:
        value = observed_run().model_dump(mode="json")
        value["observations"][0]["hits"].append(
            {"chunk_id": "escaped", "version_id": "cv-out", "rank": 3}
        )
        value["observations"][0]["citations"] = [{
            "chunk_id": "not-retrieved", "version_id": "cv-1", "quote_hash": HASH_A,
        }]
        result = evaluate_rag_run(dataset(), RagEvalRun.model_validate(value), cutoff_k=3)
        self.assertEqual(result.scope_violation_count, 1)
        self.assertEqual(result.citation_violation_count, 1)
        self.assertFalse(result.passed)
        self.assertIn("SCOPE_VIOLATIONS_ABOVE_GATE", result.failures)
        self.assertIn("CITATION_VIOLATIONS_ABOVE_GATE", result.failures)

    def test_evaluation_requires_exact_case_coverage(self) -> None:
        value = observed_run().model_dump(mode="json")
        value["observations"].pop()
        with self.assertRaisesRegex(RagEvalError, "coverage mismatch"):
            evaluate_rag_run(dataset(), RagEvalRun.model_validate(value))

    def test_quality_thresholds_are_explicit(self) -> None:
        result = evaluate_rag_run(
            dataset(), observed_run(), cutoff_k=2,
            gates=RagEvalGates(min_macro_recall_at_k=0.75),
        )
        self.assertFalse(result.passed)
        self.assertEqual(result.failures, ("MACRO_RECALL_BELOW_GATE",))

    def test_candidate_comparison_detects_improvement_and_regression(self) -> None:
        baseline = evaluate_rag_run(dataset(), observed_run(), cutoff_k=2)
        candidate = evaluate_rag_run(dataset(), observed_run(improved=True), cutoff_k=2)
        improved = compare_rag_summaries(
            baseline, candidate, min_recall_delta=0.25, min_ndcg_delta=0.0,
        )
        self.assertTrue(improved.passed)
        self.assertAlmostEqual(improved.macro_recall_delta, 0.25)
        regressed = compare_rag_summaries(candidate, baseline)
        self.assertFalse(regressed.passed)
        self.assertIn("MACRO_RECALL_REGRESSION", regressed.failures)

    def test_cli_evaluate_and_compare_write_machine_readable_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset_path = root / "dataset.json"
            baseline_path = root / "baseline-run.json"
            candidate_path = root / "candidate-run.json"
            baseline_summary = root / "baseline-summary.json"
            candidate_summary = root / "candidate-summary.json"
            comparison = root / "comparison.json"
            dataset_path.write_text(dataset().model_dump_json(indent=2), encoding="utf-8")
            baseline_path.write_text(observed_run().model_dump_json(indent=2), encoding="utf-8")
            candidate_path.write_text(
                observed_run(improved=True).model_dump_json(indent=2), encoding="utf-8"
            )
            self.assertEqual(main([
                "evaluate", "--dataset", str(dataset_path), "--run", str(baseline_path),
                "--output", str(baseline_summary), "--cutoff", "2",
            ]), 0)
            self.assertEqual(main([
                "evaluate", "--dataset", str(dataset_path), "--run", str(candidate_path),
                "--output", str(candidate_summary), "--cutoff", "2",
            ]), 0)
            self.assertEqual(main([
                "compare", "--baseline", str(baseline_summary),
                "--candidate", str(candidate_summary), "--output", str(comparison),
                "--min-recall-delta", "0.25",
            ]), 0)
            self.assertTrue(json.loads(comparison.read_text())["passed"])

    def test_collector_preserves_case_order_and_rejects_scope_escape(self) -> None:
        async def valid_search(case, _limit):
            relevant = case.relevant_chunks[0]
            return (ObservedHit(
                chunk_id=relevant.chunk_id,
                version_id=relevant.version_id,
                rank=1,
                sources=("fixture",),
            ),)

        import asyncio
        value = asyncio.run(collect_rag_run(
            dataset(), valid_search, run_id="collected", system_version="sha",
            retriever_version="fixture-v1", limit=5,
        ))
        self.assertEqual(
            [item.case_id for item in value.observations],
            [item.case_id for item in dataset().cases],
        )

        async def escaped_search(_case, _limit):
            return (ObservedHit(
                chunk_id="outside", version_id="cv-outside", rank=1,
            ),)

        with self.assertRaisesRegex(RagEvalError, "outside frozen scope"):
            asyncio.run(collect_rag_run(
                dataset(), escaped_search, run_id="bad", system_version="sha",
                retriever_version="fixture-v1",
            ))


if __name__ == "__main__":
    unittest.main()
