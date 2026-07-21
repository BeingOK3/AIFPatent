from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path

from idea.agent_schemas import IdeaParserOutput, NoveltyResult, QueryPlannerOutput, ValueAnalyzerOutput
from idea.config import load_config
from idea.database import Database
from idea.execution import WorkflowExecutor
from idea.model_client import RuntimeModelConfig, runtime_model_config
from idea.providers import FetchedDocument
from idea.retrieval import FetchResult, RetrievalResult
from idea.run_store import RunStore
from idea.search_strategy import StopReason
from idea.workflow import WORKFLOW_STEPS, WorkflowHarness, WorkflowStep


def parsed_idea():
    return IdeaParserOutput.model_validate({
        "title": "cache", "technical_domains": ["storage"], "application_scenario": "GPU",
        "technical_problem": "reduce misses",
        "features": [{"feature_id": "F1", "feature_text": "heat eviction", "source_type": "normalized"}],
        "claimed_effects": ["less work"], "subject_types": ["method"], "scope_breadth": "narrow",
    })


def query_plan():
    return QueryPlannerOutput.model_validate({
        "term_groups": [
            {"concept": "cache", "zh_terms": ["缓存"], "en_terms": ["cache"]},
            {"concept": "heat", "zh_terms": ["热度"], "en_terms": ["heat"]},
        ],
        "queries": [
            {"query_id": "Q1", "round_number": 1, "query_type": "technical_means", "language": "en", "query_text": "cache heat eviction", "rationale": "means"},
            {"query_id": "Q2", "round_number": 1, "query_type": "problem_effect", "language": "en", "query_text": "reduce cache miss", "rationale": "effect"},
        ],
    })


def novelty_result():
    return NoveltyResult.model_validate({
        "conclusion": "NOVEL", "confidence": 0.7,
        "matrices": [{
            "publication_number": "US1A1",
            "mappings": [{"feature_id": "F1", "status": "NOT_DISCLOSED", "evidence_ids": [], "rationale": "missing", "confidence": 0.8}],
            "destroys_novelty": False,
        }],
        "destroying_publication_number": None, "closest_publication_number": "US1A1",
        "missing_features": ["F1"], "rationale": "具备新颖性",
    })


def value_result():
    return ValueAnalyzerOutput.model_validate({
        "detectability": {"rating": 3, "rationale": "basis", "evidence_basis": ["IDEA:F1"]},
        "workaround_difficulty": {"rating": 3, "rationale": "basis", "evidence_basis": ["IDEA:F1"]},
        "technical_market_value": {"rating": 3, "rationale": "basis", "evidence_basis": ["IDEA:F1"]},
        "alternative_paths": ["path one", "path two"], "recommendation": "WATCH", "rationale": "preliminary",
    })


class FakeAgents:
    def __init__(self):
        self.parse_calls = 0

    async def parse_idea(self, run_id, text):
        self.parse_calls += 1
        return parsed_idea()

    async def plan_queries(self, run_id, idea, *, per_query_limit):
        return query_plan()


class BlockingAgents(FakeAgents):
    def __init__(self):
        super().__init__()
        self.started = asyncio.Event()

    async def parse_idea(self, run_id, text):
        self.started.set()
        await asyncio.Event().wait()


class FakeRetrieval:
    def __init__(self, fail_once=False, document_count=10):
        self.retrieve_calls = 0
        self.fetch_calls = 0
        self.fail_once = fail_once
        self.document_count = document_count

    async def retrieve(self, **kwargs):
        self.retrieve_calls += 1
        if self.fail_once and self.retrieve_calls == 1:
            raise ConnectionError("temporary search failure")
        return RetrievalResult(
            merged_hits=[], screened=[], selected_publication_numbers=[], provider_calls={},
            stop_reason=StopReason.QUERY_EXHAUSTED, limitations=[], rounds=[],
        )

    async def fetch_selected(self, **kwargs):
        self.fetch_calls += 1
        documents = [
            FetchedDocument(
                provider="fixture",
                publication_number=f"US{index}A1",
                url=f"https://patents.google.com/patent/US{index}A1/en",
                claims_text="1. fixture claim",
            )
            for index in range(1, self.document_count + 1)
        ]
        limitations = []
        if self.document_count < kwargs["minimum_documents"]:
            limitations.append({
                "code": "DEEP_REVIEW_FETCHED_BELOW_MINIMUM",
                "required": kwargs["minimum_documents"],
                "fetched": self.document_count,
            })
        return FetchResult(
            documents=documents,
            document_ids={item.publication_number: f"doc-{index}" for index, item in enumerate(documents)},
            limitations=limitations,
        )


class FakeDocuments:
    def __init__(self):
        self.legacy_calls = []
        self.rag_calls = []

    async def analyze_many(self, **kwargs):
        self.legacy_calls.append(kwargs)
        return {}

    async def analyze_many_rag(self, **kwargs):
        self.rag_calls.append(kwargs)
        return {}


class FakeCorpusIngest:
    def __init__(self):
        self.calls = []
        self.reviewed = []
        self.synced = []

    async def ingest_many(self, **kwargs):
        self.calls.append(kwargs)
        return type(
            "CorpusIngestResult",
            (),
            {"version_ids": ("cv-1",), "snapshot_hash": "a" * 64},
        )()

    async def mark_deep_reviewed(self, run_id, document_ids):
        self.reviewed.append((run_id, document_ids))

    async def sync_run_status(self, run_id):
        self.synced.append(run_id)
        return True


class FakeReportRag:
    def __init__(self, corpus):
        self.corpus = corpus
        self.calls = []

    async def prepare(self, **kwargs):
        if self.corpus.reviewed:
            raise AssertionError("RAG context must be built before deep-review state is durable")
        self.calls.append(kwargs)
        return type(
            "PreparedRag",
            (),
            {
                "context_ids": ("CTX-fixture",),
                "retrieval_query_count": 1,
                "contexts": (),
                "limitations": (),
            },
        )()


class FakeNovelty:
    def determine(self, run_id, **kwargs):
        return novelty_result()


class FakeInventiveness:
    async def analyze(self, run_id, novelty):
        return []


class FakeValue:
    async def analyze(self, *args):
        return value_result()


class FakeAudit:
    def __init__(self, critical=False):
        self.critical = critical
        self.calls = 0

    async def audit(self, *args):
        self.calls += 1
        return [{
            "severity": "critical" if self.critical else "info",
            "code": "FIXTURE_AUDIT", "message": "fixture", "details": {},
        }]


class FakeReporting:
    def __init__(self, database, store):
        self.database = database
        self.store = store
        self.calls = 0

    async def generate(self, run_id, *args, **kwargs):
        self.calls += 1
        run = self.database.get_run(run_id)
        report = {
            "schema_version": "test",
            "audit": {"counts": {"critical": 0, "warning": 0, "info": 1}},
        }
        self.store.write_reports(
            run["case_id"], run_id, report=report, markdown="# test\n",
            manifest_metadata={"fixture": True},
        )
        return report


class CorruptReporting(FakeReporting):
    async def generate(self, run_id, *args, **kwargs):
        report = await super().generate(run_id, *args, **kwargs)
        run = self.database.get_run(run_id)
        self.store.paths(run["case_id"], run_id).report_md.write_text(
            "corrupted", encoding="utf-8"
        )
        return report


class LimitedReporting(FakeReporting):
    async def generate(self, run_id, *args, **kwargs):
        self.calls += 1
        run = self.database.get_run(run_id)
        report = {
            "schema_version": "test",
            "audit": {"counts": {"critical": 0, "warning": 0, "info": 1}},
            "limitations": [
                {
                    "code": "PROVIDER_CALL_FAILURE",
                    "message": "fixture provider fetch returned CONTRACT_ERROR",
                }
            ],
        }
        self.store.write_reports(
            run["case_id"], run_id, report=report, markdown="# limited test\n",
            manifest_metadata={"fixture": True},
        )
        return report


class WorkflowExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.db = Database(root / "idea.db")
        self.db.initialize()
        self.store = RunStore(root / "runs")
        self.config = load_config()
        disabled_features = self.config.features.model_copy(
            update={
                "patent_corpus": False,
                "initial_review_rag": False,
                "followup_rag": False,
            }
        )
        self.config = self.config.model_copy(update={"features": disabled_features})
        self.graph_db = root / "langgraph.db"
        storage = self.config.storage.model_copy(
            update={"langgraph_database": self.graph_db}
        )
        workflow = self.config.workflow.model_copy(
            update={"max_step_attempts": 2, "step_timeout_seconds": 5}
        )
        self.config = self.config.model_copy(
            update={"workflow": workflow, "storage": storage}
        )
        self.case = self.db.create_case("Execution")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def create_run(self):
        return self.db.create_run(
            case_id=self.case["case_id"], input_text="idea", evaluation_date="2026-07-16",
            date_basis="default", analysis_scope="full", model="stub", skill_version="1",
            workflow_version="1", config_snapshot={}, settings={"search_mode": "quick"},
        )

    def executor(
        self,
        run,
        *,
        retrieval=None,
        audit=None,
        agents=None,
        reporting=None,
        corpus_ingest=None,
        report_rag=None,
        config=None,
    ):
        harness = WorkflowHarness(self.db, self.store, max_step_attempts=2)
        return WorkflowExecutor(
            config or self.config, self.db, self.store, harness, agents or FakeAgents(),
            retrieval or FakeRetrieval(), FakeDocuments(), FakeNovelty(),
            FakeInventiveness(), FakeValue(), audit or FakeAudit(),
            reporting or FakeReporting(self.db, self.store),
            corpus_ingest=corpus_ingest,
            report_rag=report_rag,
        )

    def test_enabled_corpus_is_ingested_before_document_analysis(self) -> None:
        run = self.create_run()
        features = self.config.features.model_copy(update={"patent_corpus": True})
        config = self.config.model_copy(update={"features": features})
        corpus = FakeCorpusIngest()
        executor = self.executor(run, config=config, corpus_ingest=corpus)

        status = self.run_executor(executor, run["run_id"])

        self.assertEqual(status, "COMPLETED")
        self.assertEqual(len(corpus.calls), 1)
        self.assertEqual(corpus.synced, [run["run_id"]])
        self.assertEqual(corpus.reviewed[0][0], run["run_id"])
        self.assertEqual(
            set(corpus.reviewed[0][1]), set(corpus.calls[0]["document_ids"].values())
        )
        checkpoint = executor._checkpoint(run["run_id"], WorkflowStep.NORMALIZE_AND_FETCH)
        self.assertEqual(checkpoint["corpus_version_ids"], ["cv-1"])
        self.assertEqual(checkpoint["corpus_snapshot_hash"], "a" * 64)

    def test_enabled_initial_report_rag_freezes_context_before_deep_review(self) -> None:
        run = self.create_run()
        features = self.config.features.model_copy(
            update={"patent_corpus": True, "initial_review_rag": True}
        )
        config = self.config.model_copy(update={"features": features})
        corpus = FakeCorpusIngest()
        rag = FakeReportRag(corpus)
        executor = self.executor(
            run, config=config, corpus_ingest=corpus, report_rag=rag
        )

        status = self.run_executor(executor, run["run_id"])

        self.assertEqual(status, "COMPLETED")
        self.assertEqual(len(rag.calls), 1)
        self.assertEqual(rag.calls[0]["corpus_snapshot_hash"], "a" * 64)
        self.assertEqual(rag.calls[0]["allowed_version_ids"], ("cv-1",))
        checkpoint = executor._checkpoint(run["run_id"], WorkflowStep.ANALYZE_DOCUMENTS)
        self.assertEqual(checkpoint["rag_context_ids"], ["CTX-fixture"])

    def test_enabled_initial_report_rag_fails_closed_without_service(self) -> None:
        run = self.create_run()
        features = self.config.features.model_copy(
            update={"patent_corpus": True, "initial_review_rag": True}
        )
        config = self.config.model_copy(update={"features": features})
        executor = self.executor(
            run, config=config, corpus_ingest=FakeCorpusIngest(), report_rag=None
        )

        status = self.run_executor(executor, run["run_id"])

        self.assertEqual(status, "FAILED")
        self.assertIn("initial report RAG service is required", self.db.get_run(run["run_id"])["error_message"])

    def test_enabled_corpus_fails_closed_without_ingest_service(self) -> None:
        run = self.create_run()
        features = self.config.features.model_copy(update={"patent_corpus": True})
        config = self.config.model_copy(update={"features": features})
        executor = self.executor(run, config=config)

        status = self.run_executor(executor, run["run_id"])

        self.assertEqual(status, "FAILED")
        self.assertIn(
            "corpus ingest service is required",
            self.db.get_run(run["run_id"])["error_message"],
        )

    @staticmethod
    def run_executor(executor, run_id):
        async def scenario():
            try:
                return await executor.execute(run_id)
            finally:
                await executor.aclose()

        return asyncio.run(scenario())

    def test_fixed_graph_completes_all_eleven_steps_and_manifest(self) -> None:
        run = self.create_run()
        executor = self.executor(run)
        status = self.run_executor(executor, run["run_id"])
        self.assertEqual(status, "COMPLETED")
        progress = executor.harness.progress(run["run_id"])
        self.assertEqual(progress["completed_steps"], len(WORKFLOW_STEPS))
        self.store.verify(self.case["case_id"], run["run_id"])

    def test_transient_step_failure_retries_only_that_step(self) -> None:
        run = self.create_run()
        retrieval = FakeRetrieval(fail_once=True)
        executor = self.executor(run, retrieval=retrieval)
        status = self.run_executor(executor, run["run_id"])
        self.assertEqual(status, "COMPLETED")
        self.assertEqual(retrieval.retrieve_calls, 2)
        with self.db.connect() as connection:
            attempts = connection.execute(
                "SELECT COUNT(*) FROM run_steps WHERE run_id = ? AND step_name = 'RETRIEVE_CANDIDATES'",
                (run["run_id"],),
            ).fetchone()[0]
        self.assertEqual(attempts, 2)

    def test_partial_deep_review_continues_once_with_explicit_limitation(self) -> None:
        run = self.create_run()
        retrieval = FakeRetrieval(document_count=4)
        executor = self.executor(run, retrieval=retrieval)

        status = self.run_executor(executor, run["run_id"])

        self.assertEqual(status, "COMPLETED_WITH_LIMITATIONS")
        self.assertEqual(retrieval.fetch_calls, 1)
        stored = self.db.get_run(run["run_id"])
        self.assertIn(
            "DEEP_REVIEW_FETCHED_BELOW_MINIMUM",
            {item["code"] for item in stored["limitation_json"]},
        )

    def test_report_limitations_produce_completed_with_limitations_terminal_state(self) -> None:
        run = self.create_run()
        executor = self.executor(
            run, reporting=LimitedReporting(self.db, self.store)
        )
        status = self.run_executor(executor, run["run_id"])
        self.assertEqual(status, "COMPLETED_WITH_LIMITATIONS")
        stored = self.db.get_run(run["run_id"])
        self.assertEqual(stored["status"], "COMPLETED_WITH_LIMITATIONS")
        self.assertEqual(
            stored["limitation_json"][0]["code"], "PROVIDER_CALL_FAILURE"
        )

    def test_write_once_parser_checkpoint_is_reused_after_crash_window(self) -> None:
        run = self.create_run()
        self.db.put_stage_result(run["run_id"], "PARSE_IDEA", parsed_idea().model_dump(mode="json"))
        agents = FakeAgents()
        executor = self.executor(run, agents=agents)
        self.assertEqual(self.run_executor(executor, run["run_id"]), "COMPLETED")
        self.assertEqual(agents.parse_calls, 0)

    def test_critical_audit_exhausts_step_and_never_writes_report(self) -> None:
        run = self.create_run()
        audit = FakeAudit(critical=True)
        executor = self.executor(run, audit=audit)
        self.assertEqual(self.run_executor(executor, run["run_id"]), "FAILED")
        self.assertEqual(audit.calls, 2)
        self.assertFalse(self.store.paths(self.case["case_id"], run["run_id"]).manifest.exists())

    def test_manifest_completion_gate_marks_run_failed(self) -> None:
        run = self.create_run()
        executor = self.executor(
            run, reporting=CorruptReporting(self.db, self.store)
        )
        self.assertEqual(self.run_executor(executor, run["run_id"]), "FAILED")
        stored = self.db.get_run(run["run_id"])
        self.assertEqual(stored["error_code"], "COMPLETION_GATE_FAILED")

    def test_graph_has_fixed_nodes_and_checkpoint_never_contains_runtime_secret(self) -> None:
        run = self.create_run()
        executor = self.executor(run)
        secret = "sentinel-runtime-secret-must-not-be-persisted"

        async def scenario():
            try:
                graph = await executor.graph._compiled_graph()
                nodes = set(graph.nodes)
                status = await executor.execute(run["run_id"])
                return status, nodes
            finally:
                await executor.aclose()

        with runtime_model_config(
            RuntimeModelConfig("https://runtime.test/v1", secret, "fixture-model")
        ):
            status, nodes = asyncio.run(scenario())
        self.assertEqual(status, "COMPLETED")
        self.assertTrue({step.value for step in WORKFLOW_STEPS}.issubset(nodes))
        self.assertTrue(self.graph_db.is_file())
        self.assertNotIn(secret.encode(), self.graph_db.read_bytes())

    def test_two_runs_execute_concurrently_on_independent_graph_threads(self) -> None:
        first = self.create_run()
        second = self.create_run()
        executor = self.executor(first)

        async def scenario():
            try:
                return await asyncio.gather(
                    executor.execute(first["run_id"]),
                    executor.execute(second["run_id"]),
                )
            finally:
                await executor.aclose()

        self.assertEqual(asyncio.run(scenario()), ["COMPLETED", "COMPLETED"])
        with sqlite3.connect(self.graph_db) as connection:
            thread_count = connection.execute(
                "SELECT COUNT(DISTINCT thread_id) FROM checkpoints"
            ).fetchone()[0]
        self.assertEqual(thread_count, 2)

    def test_cancelling_graph_interrupts_active_node_and_run(self) -> None:
        run = self.create_run()
        agents = BlockingAgents()
        executor = self.executor(run, agents=agents)

        async def scenario():
            task = asyncio.create_task(executor.execute(run["run_id"]))
            try:
                await asyncio.wait_for(agents.started.wait(), timeout=2)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                return self.db.get_run(run["run_id"]), executor.harness.progress(run["run_id"])
            finally:
                await executor.aclose()

        stored, progress = asyncio.run(scenario())
        self.assertEqual(stored["status"], "CANCELLED")
        parse_step = next(item for item in progress["steps"] if item["name"] == "PARSE_IDEA")
        self.assertEqual(parse_step["status"], "INTERRUPTED")


if __name__ == "__main__":
    unittest.main()
