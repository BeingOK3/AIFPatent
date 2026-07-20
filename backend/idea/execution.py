from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

from .agent_schemas import (
    IdeaParserOutput,
    InventiveStepOutput,
    NoveltyResult,
    QueryPlannerOutput,
    ValueAnalyzerOutput,
)
from .agents import IdeaAgentService
from .audit import AuditService
from .config import AppConfig
from .database import Database
from .document_analysis import DocumentAnalysisService
from .inventiveness import InventivenessService
from .graph import LangGraphWorkflow
from .novelty import NoveltyService
from .providers import FetchedDocument
from .reporting import ReportService
from .retrieval import RetrievalResult, RetrievalService
from .run_store import RunStore, RunStoreError
from .runtime_debug import RunDebugLog
from .search_strategy import assess_breadth, build_budget
from .value_analysis import ValueAnalysisService
from .workflow import WorkflowHarness, WorkflowStep


class ExecutionGateError(RuntimeError):
    pass


class WorkflowExecutor:
    """Runs the fixed 11-step IDEA graph; models never choose control flow."""

    def __init__(
        self,
        config: AppConfig,
        database: Database,
        run_store: RunStore,
        harness: WorkflowHarness,
        agents: IdeaAgentService,
        retrieval: RetrievalService,
        documents: DocumentAnalysisService,
        novelty: NoveltyService,
        inventiveness: InventivenessService,
        value: ValueAnalysisService,
        audit: AuditService,
        reporting: ReportService,
        *,
        debug_log: RunDebugLog | None = None,
    ):
        self.config = config
        self.database = database
        self.run_store = run_store
        self.harness = harness
        self.agents = agents
        self.retrieval = retrieval
        self.documents = documents
        self.novelty = novelty
        self.inventiveness = inventiveness
        self.value = value
        self.audit = audit
        self.reporting = reporting
        self.debug_log = debug_log
        self.graph = LangGraphWorkflow(
            database=database,
            harness=harness,
            checkpoint_path=config.storage.langgraph_database,
            step_handler=self._execute_step,
            fingerprint_builder=self._step_input_fingerprint,
            limitation_collector=self._collect_run_limitations,
            step_timeout_seconds=config.workflow.step_timeout_seconds,
            max_step_attempts=config.workflow.max_step_attempts,
            debug_log=debug_log,
        )

    async def execute(self, run_id: str) -> str:
        return await self.graph.execute(run_id)

    async def aclose(self) -> None:
        await self.graph.aclose()

    def _debug(self, run_id: str, event: str, **details: Any) -> None:
        if self.debug_log:
            self.debug_log.append(run_id, event, **details)

    async def _execute_step(
        self, run_id: str, step: WorkflowStep, attempt: int
    ) -> dict[str, Any]:
        if step == WorkflowStep.PREPARE_INPUT:
            return self._prepare_input(run_id)
        if step == WorkflowStep.PARSE_IDEA:
            checkpoint = self._checkpoint(run_id, step)
            if checkpoint:
                return checkpoint
            run = self.database.get_run(run_id)
            output = await self.agents.parse_idea(run_id, run["input_text"])
            return self._save_checkpoint(run_id, step, output.model_dump(mode="json"))
        if step == WorkflowStep.VALIDATE_IDEA_MODEL:
            checkpoint = self._checkpoint(run_id, step)
            if checkpoint:
                return checkpoint
            idea = self._idea(run_id)
            required = [feature for feature in idea.features if feature.required]
            expected = [f"F{index}" for index in range(1, len(idea.features) + 1)]
            actual = [feature.feature_id for feature in idea.features]
            if actual != expected:
                raise ExecutionGateError("idea feature IDs must be contiguous and ordered F1..Fn")
            if not required:
                raise ExecutionGateError("idea has no required technical feature")
            value = {
                "valid": True,
                "feature_count": len(idea.features),
                "required_feature_count": len(required),
                "scope_breadth": idea.scope_breadth,
            }
            return self._save_checkpoint(run_id, step, value)
        if step == WorkflowStep.PLAN_QUERIES:
            checkpoint = self._checkpoint(run_id, step)
            if checkpoint:
                return checkpoint
            idea = self._idea(run_id)
            budget = self._budget(run_id, idea)
            output = await self.agents.plan_queries(
                run_id, idea, per_query_limit=budget.per_query_limit
            )
            return self._save_checkpoint(run_id, step, output.model_dump(mode="json"))
        if step == WorkflowStep.RETRIEVE_CANDIDATES:
            checkpoint = self._checkpoint(run_id, step)
            if checkpoint:
                return checkpoint
            if attempt > 1:
                self._clear_retrieval_attempt(run_id)
            idea = self._idea(run_id)
            plan = self._plan(run_id)
            budget = self._budget(run_id, idea)
            run = self.database.get_run(run_id)
            output = await self.retrieval.retrieve(
                run_id=run_id,
                plan=plan,
                budget=budget,
                idea_terms=self._idea_terms(idea),
                evaluation_date=date.fromisoformat(run["evaluation_date"]),
                saturation_rounds=self.config.search.saturation.consecutive_rounds,
                saturation_new_high_max=self.config.search.saturation.max_new_high_relevance_families,
            )
            return self._save_checkpoint(run_id, step, output.model_dump(mode="json"))
        if step == WorkflowStep.NORMALIZE_AND_FETCH:
            checkpoint = self._checkpoint(run_id, step)
            if checkpoint:
                return checkpoint
            if attempt > 1:
                self._clear_fetch_attempt(run_id)
            retrieval = self._retrieval(run_id)
            output = await self.retrieval.fetch_selected(
                run_id=run_id,
                retrieval=retrieval,
                minimum_documents=self._budget(run_id, self._idea(run_id)).deep_review_min,
            )
            if not output.documents:
                raise ExecutionGateError(
                    "no patent full text is available for evidence-based analysis"
                )
            value = {
                "publication_numbers": [item.publication_number for item in output.documents],
                "document_ids": output.document_ids,
                "limitations": output.limitations,
            }
            return self._save_checkpoint(run_id, step, value)
        if step == WorkflowStep.ANALYZE_DOCUMENTS:
            checkpoint = self._checkpoint(run_id, step)
            if checkpoint:
                return checkpoint
            idea = self._idea(run_id)
            fetched, document_ids = self._pending_documents(run_id)
            if fetched:
                await self.documents.analyze_many(
                    run_id=run_id,
                    idea=idea,
                    documents=fetched,
                    document_ids=document_ids,
                )
            with self.database.connect() as connection:
                reviewed = connection.execute(
                    "SELECT COUNT(*) FROM run_documents WHERE run_id = ? AND deep_reviewed = 1",
                    (run_id,),
                ).fetchone()[0]
                pending = connection.execute(
                    "SELECT COUNT(*) FROM run_documents WHERE run_id = ? AND deep_reviewed = 0",
                    (run_id,),
                ).fetchone()[0]
            if pending:
                raise ExecutionGateError(f"{pending} fetched documents remain unanalyzed")
            return self._save_checkpoint(
                run_id, step, {"deep_reviewed_count": reviewed, "pending_count": pending}
            )
        if step == WorkflowStep.DETERMINE_NOVELTY:
            checkpoint = self._checkpoint(run_id, step)
            if checkpoint:
                return checkpoint
            existing = self._load_novelty_from_database(run_id)
            output = existing or self.novelty.determine(
                run_id,
                minimum_deep_reviews=self._budget(run_id, self._idea(run_id)).deep_review_min,
            )
            return self._save_checkpoint(run_id, step, output.model_dump(mode="json"))
        if step == WorkflowStep.ANALYZE_INVENTIVENESS:
            checkpoint = self._checkpoint(run_id, step)
            if checkpoint:
                return checkpoint
            existing = self._load_inventive_from_database(run_id)
            output = existing
            if output is None:
                output = await self.inventiveness.analyze(run_id, self._novelty(run_id))
            return self._save_checkpoint(
                run_id, step, {"routes": [item.model_dump(mode="json") for item in output]}
            )
        if step == WorkflowStep.ASSESS_VALUE:
            checkpoint = self._checkpoint(run_id, step)
            if checkpoint:
                return checkpoint
            existing = self._load_value_from_database(run_id)
            output = existing or await self.value.analyze(
                run_id, self._idea(run_id), self._novelty(run_id), self._inventive(run_id)
            )
            return self._save_checkpoint(run_id, step, output.model_dump(mode="json"))
        if step == WorkflowStep.AUDIT_AND_REPORT:
            checkpoint = self._checkpoint(run_id, step)
            if checkpoint:
                return checkpoint
            findings = self._load_audit_from_database(run_id)
            if findings is None:
                findings = await self.audit.audit(
                    run_id,
                    self._novelty(run_id),
                    self._inventive(run_id),
                    self._value(run_id),
                )
            critical = [item for item in findings if item["severity"] == "critical"]
            if critical:
                raise ExecutionGateError(f"critical audit findings: {len(critical)}")
            existing_report = self._load_report_from_database(run_id)
            report = existing_report or await self.reporting.generate(
                run_id,
                self._idea(run_id),
                self._novelty(run_id),
                self._inventive(run_id),
                self._value(run_id),
                findings,
                limitations=self._collect_run_limitations(run_id),
            )
            return self._save_checkpoint(
                run_id,
                step,
                {
                    "report_schema_version": report["schema_version"],
                    "audit_counts": report["audit"]["counts"],
                    "limitations": report.get("limitations", []),
                },
            )
        raise ExecutionGateError(f"unsupported Workflow step: {step}")

    def _prepare_input(self, run_id: str) -> dict[str, Any]:
        checkpoint = self._checkpoint(run_id, WorkflowStep.PREPARE_INPUT)
        if checkpoint:
            return checkpoint
        run = self.database.get_run(run_id)
        paths = self.run_store.paths(run["case_id"], run_id)
        if not paths.root.exists():
            self.run_store.initialize_run(run["case_id"], run_id)
        elif not paths.input_dir.is_dir() or not paths.artifacts_dir.is_dir():
            raise RunStoreError("existing run layout is incomplete")
        input_record_path = paths.input_dir / "input.json"
        if input_record_path.is_file():
            record = json.loads(input_record_path.read_text(encoding="utf-8"))
            if record.get("idea", {}).get("sha256") != run["input_hash"]:
                raise RunStoreError("input snapshot hash does not match immutable run input")
        else:
            attachments = []
            for item in run["attachments_json"]:
                stored_path = item.get("stored_path") if isinstance(item, dict) else None
                if stored_path:
                    attachments.append(Path(stored_path))
            record = self.run_store.snapshot_input(
                run["case_id"],
                run_id,
                input_text=run["input_text"],
                metadata={
                    "evaluation_date": run["evaluation_date"],
                    "date_basis": run["date_basis"],
                    "settings": run["settings_json"],
                },
                attachments=attachments,
            )
        return self._save_checkpoint(run_id, WorkflowStep.PREPARE_INPUT, record)

    def _pending_documents(self, run_id: str) -> tuple[list[FetchedDocument], dict[str, str]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT d.* FROM run_documents rd
                JOIN patent_documents d ON d.document_id = rd.document_id
                WHERE rd.run_id = ? AND rd.deep_reviewed = 0 ORDER BY d.publication_number""",
                (run_id,),
            ).fetchall()
        documents = []
        ids = {}
        for row in rows:
            metadata = self._json(row["metadata_json"], {})
            spans = metadata.pop("section_spans", {})
            abstract = row["abstract_text"] or ""
            claims = row["claims_text"] or ""
            description = row["description_text"] or ""
            content = "\n\n".join([abstract, claims, description])
            if not content.strip():
                raise ExecutionGateError(
                    f"fetched document text is unavailable: {row['publication_number']}"
                )
            if hashlib.sha256(content.encode("utf-8")).hexdigest() != row["content_hash"]:
                raise ExecutionGateError(
                    f"fetched document content hash mismatch: {row['publication_number']}"
                )
            document = FetchedDocument(
                provider=str(metadata.get("source") or metadata.get("parser") or "stored"),
                publication_number=row["publication_number"],
                application_number=row["application_number"],
                family_id=row["family_id"],
                title=row["title"] or "",
                assignee=row["assignee"],
                inventors=self._json(row["inventors_json"], []),
                priority_date=row["priority_date"],
                filing_date=row["filing_date"],
                publication_date=row["publication_date"],
                grant_date=row["grant_date"],
                language=row["language"] or "en",
                url=row["url"] or "https://patents.google.com",
                abstract_text=abstract,
                claims_text=claims,
                description_text=description,
                section_spans=spans,
                raw_metadata=metadata,
            )
            documents.append(document)
            ids[document.publication_number] = row["document_id"]
        return documents, ids

    def _idea(self, run_id: str) -> IdeaParserOutput:
        return IdeaParserOutput.model_validate(self._required_checkpoint(run_id, "PARSE_IDEA"))

    def _plan(self, run_id: str) -> QueryPlannerOutput:
        return QueryPlannerOutput.model_validate(self._required_checkpoint(run_id, "PLAN_QUERIES"))

    def _retrieval(self, run_id: str) -> RetrievalResult:
        return RetrievalResult.model_validate(
            self._required_checkpoint(run_id, "RETRIEVE_CANDIDATES")
        )

    def _novelty(self, run_id: str) -> NoveltyResult:
        return NoveltyResult.model_validate(
            self._required_checkpoint(run_id, "DETERMINE_NOVELTY")
        )

    def _inventive(self, run_id: str) -> list[InventiveStepOutput]:
        value = self._required_checkpoint(run_id, "ANALYZE_INVENTIVENESS")
        return [InventiveStepOutput.model_validate(item) for item in value["routes"]]

    def _value(self, run_id: str) -> ValueAnalyzerOutput:
        return ValueAnalyzerOutput.model_validate(
            self._required_checkpoint(run_id, "ASSESS_VALUE")
        )

    def _budget(self, run_id: str, idea: IdeaParserOutput):
        settings = self.database.get_run(run_id)["settings_json"]
        breadth = assess_breadth(
            technical_domains=idea.technical_domains,
            features=[feature.feature_text for feature in idea.features if feature.required],
            search_terms=self._idea_terms(idea),
            explicit=idea.scope_breadth,
        )
        return build_budget(
            self.config.search,
            breadth,
            mode_name=settings.get("search_mode"),
            candidate_max=settings.get("candidate_max"),
            deep_review_min=settings.get("deep_review_min"),
            deep_review_max=settings.get("deep_review_max"),
        )

    @staticmethod
    def _idea_terms(idea: IdeaParserOutput) -> list[str]:
        return [
            *idea.technical_domains,
            *(feature.feature_text for feature in idea.features if feature.required),
            *idea.claimed_effects,
        ]

    def _load_novelty_from_database(self, run_id: str) -> NoveltyResult | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT matrix_json FROM novelty_results WHERE run_id = ?", (run_id,)
            ).fetchone()
        return NoveltyResult.model_validate(json.loads(row["matrix_json"])) if row else None

    def _load_inventive_from_database(
        self, run_id: str
    ) -> list[InventiveStepOutput] | None:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT result_json FROM inventive_routes WHERE run_id = ? ORDER BY route_id",
                (run_id,),
            ).fetchall()
        if not rows:
            return None
        return [InventiveStepOutput.model_validate(json.loads(row["result_json"])) for row in rows]

    def _load_value_from_database(self, run_id: str) -> ValueAnalyzerOutput | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT result_json FROM value_results WHERE run_id = ?", (run_id,)
            ).fetchone()
        return ValueAnalyzerOutput.model_validate(json.loads(row["result_json"])) if row else None

    def _load_audit_from_database(self, run_id: str) -> list[dict[str, Any]] | None:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT severity,code,message,details_json FROM audit_results WHERE run_id = ?",
                (run_id,),
            ).fetchall()
        if not rows:
            return None
        return [
            {
                "severity": row["severity"],
                "code": row["code"],
                "message": row["message"],
                "details": self._json(row["details_json"], {}),
            }
            for row in rows
        ]

    def _load_report_from_database(self, run_id: str) -> dict[str, Any] | None:
        run = self.database.get_run(run_id)
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT report_json_path FROM reports WHERE run_id = ?", (run_id,)
            ).fetchone()
        if not row:
            return None
        self.run_store.verify(run["case_id"], run_id)
        return json.loads(Path(row["report_json_path"]).read_text(encoding="utf-8"))

    def _clear_retrieval_attempt(self, run_id: str) -> None:
        with self.database.connect() as connection:
            connection.execute("DELETE FROM search_hits WHERE run_id = ?", (run_id,))
            connection.execute(
                "DELETE FROM tool_calls WHERE run_id = ? AND step_name = 'RETRIEVE_CANDIDATES'",
                (run_id,),
            )

    def _clear_fetch_attempt(self, run_id: str) -> None:
        with self.database.connect() as connection:
            connection.execute(
                "DELETE FROM run_documents WHERE run_id = ? AND deep_reviewed = 0", (run_id,)
            )
            connection.execute(
                "DELETE FROM tool_calls WHERE run_id = ? AND step_name = 'NORMALIZE_AND_FETCH'",
                (run_id,),
            )

    def _collect_run_limitations(self, run_id: str) -> list[dict[str, Any]]:
        limitations = []
        for stage in (
            "RETRIEVE_CANDIDATES",
            "NORMALIZE_AND_FETCH",
            "AUDIT_AND_REPORT",
        ):
            checkpoint = self.database.get_stage_result(run_id, stage)
            if checkpoint:
                limitations.extend(checkpoint["value"].get("limitations", []))
        novelty = self.database.get_stage_result(run_id, "DETERMINE_NOVELTY")
        if novelty:
            limitations.extend(
                {"code": "NOVELTY_LIMITATION", "message": item}
                for item in novelty["value"].get("limitations", [])
            )
        audit = self._load_audit_from_database(run_id) or []
        limitations.extend(
            {
                "code": item["code"],
                "message": item["message"],
                "severity": item["severity"],
            }
            for item in audit
            if item["severity"] == "warning"
        )
        unique = {}
        for item in limitations:
            key = json.dumps(item, ensure_ascii=False, sort_keys=True)
            unique[key] = item
        return list(unique.values())

    def _step_input_fingerprint(self, run_id: str, step: WorkflowStep) -> dict[str, Any]:
        return {
            "run_id": run_id,
            "step": step.value,
            "predecessor": self.harness.progress(run_id)["completed_steps"],
        }

    def _checkpoint(self, run_id: str, step: WorkflowStep) -> dict[str, Any] | None:
        item = self.database.get_stage_result(run_id, step.value)
        return item["value"] if item else None

    def _required_checkpoint(self, run_id: str, stage: str) -> dict[str, Any]:
        item = self.database.get_stage_result(run_id, stage)
        if item is None:
            raise ExecutionGateError(f"required stage checkpoint is missing: {stage}")
        return item["value"]

    def _save_checkpoint(
        self, run_id: str, step: WorkflowStep, value: dict[str, Any]
    ) -> dict[str, Any]:
        return self.database.put_stage_result(run_id, step.value, value)["value"]

    @staticmethod
    def _json(value: str, default):
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return default
