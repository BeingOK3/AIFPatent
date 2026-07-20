from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any

from .agent_schemas import IdeaParserOutput, QueryPlannerOutput
from .database import Database, canonical_json, now_ms
from .model_client import AgentCallResult, StructuredModelClient
from .runtime_debug import RunDebugLog


IDEA_PARSER_PROMPT = """
You are patent-idea-parser. Parse only the supplied invention text. Identify the technical
domains, application scenario, objective technical problem, claimed effects, subject types,
and a complete ordered F1..Fn list of required technical features. Mark directly quoted or
faithfully extracted features as explicit and copy their source text exactly from the input.
Provide best-effort zero-based start/end offsets; the backend resolves the exact occurrence from
the quoted source text. Mark normalization and inference honestly.
Do not search, assess novelty, cite patents, or invent missing implementation details.
"""


QUERY_PLANNER_PROMPT = """
You are patent-query-planner. Build executable Chinese and English patent search queries from
the supplied validated idea analysis. Include at least one technical-means query and one
problem/effect query. Use real terms, synonyms, broader terms and optional IPC/CPC candidates.
Do not use placeholders. Do not execute a search and do not claim any result was found.
"""


PLACEHOLDER_PATTERN = re.compile(
    r"\{[^{}]+\}|\[(?:填入|待定|关键词|keyword|placeholder)[^\]]*\]|<[^<>]+>|\b(?:TODO|TBD)\b",
    flags=re.IGNORECASE,
)


class AgentExecutionError(RuntimeError):
    pass


class IdeaAgentService:
    def __init__(
        self,
        database: Database,
        model: StructuredModelClient,
        *,
        debug_log: RunDebugLog | None = None,
    ):
        self.database = database
        self.model = model
        self.debug_log = debug_log

    async def parse_idea(self, run_id: str, idea_text: str) -> IdeaParserOutput:
        result = await self.call_agent(
            run_id,
            "patent-idea-parser",
            system_prompt=IDEA_PARSER_PROMPT,
            input_payload={"idea_text": idea_text},
            input_size=len(idea_text),
        )
        output = result.output
        if not isinstance(output, IdeaParserOutput):
            raise AgentExecutionError("idea parser returned wrong validated model")
        output = self._resolve_source_spans(idea_text, output)
        with self.database.connect() as connection:
            existing = connection.execute(
                "SELECT COUNT(*) FROM idea_features WHERE run_id = ?", (run_id,)
            ).fetchone()[0]
            if existing:
                raise AgentExecutionError("validated idea features already exist for this run")
            for ordinal, feature in enumerate(output.features, start=1):
                span = feature.source_span
                connection.execute(
                    """
                    INSERT INTO idea_features(
                        feature_id,run_id,ordinal,feature_text,source_type,
                        source_start,source_end,metadata_json
                    ) VALUES(?,?,?,?,?,?,?,?)
                    """,
                    (
                        f"{run_id}:{feature.feature_id}",
                        run_id,
                        ordinal,
                        feature.feature_text,
                        feature.source_type,
                        span.start if span else None,
                        span.end if span else None,
                        canonical_json(
                            {
                                "external_feature_id": feature.feature_id,
                                "required": feature.required,
                                "source_text": span.text if span else None,
                            }
                        ),
                    ),
                )
            self._persist_stage_result(
                connection, run_id, "PARSE_IDEA", output.model_dump(mode="json")
            )
        return output

    async def plan_queries(
        self, run_id: str, idea: IdeaParserOutput, *, per_query_limit: int
    ) -> QueryPlannerOutput:
        result = await self.call_agent(
            run_id,
            "patent-query-planner",
            system_prompt=QUERY_PLANNER_PROMPT,
            input_payload={
                "idea_analysis": idea.model_dump(mode="json"),
                "per_query_result_limit": per_query_limit,
            },
            input_size=len(canonical_json(idea.model_dump(mode="json"))),
        )
        output = result.output
        if not isinstance(output, QueryPlannerOutput):
            raise AgentExecutionError("query planner returned wrong validated model")
        for query in output.queries:
            if PLACEHOLDER_PATTERN.search(query.query_text):
                raise AgentExecutionError(f"query contains placeholder: {query.query_id}")
        with self.database.connect() as connection:
            existing = connection.execute(
                "SELECT COUNT(*) FROM search_queries WHERE run_id = ?", (run_id,)
            ).fetchone()[0]
            if existing:
                raise AgentExecutionError("validated search queries already exist for this run")
            timestamp = now_ms()
            for query in output.queries:
                connection.execute(
                    """
                    INSERT INTO search_queries(
                        query_id,run_id,round_number,query_type,language,query_text,rationale,created_at
                    ) VALUES(?,?,?,?,?,?,?,?)
                    """,
                    (
                        f"{run_id}:{query.query_id}",
                        run_id,
                        query.round_number,
                        query.query_type,
                        query.language,
                        query.query_text,
                        query.rationale,
                        timestamp,
                    ),
                )
            self._persist_stage_result(
                connection, run_id, "PLAN_QUERIES", output.model_dump(mode="json")
            )
        return output

    async def call_agent(
        self,
        run_id: str,
        agent_name: str,
        *,
        system_prompt: str,
        input_payload: dict[str, Any],
        input_size: int,
    ) -> AgentCallResult:
        call_id = str(uuid.uuid4())
        timestamp = now_ms()
        if self.debug_log:
            self.debug_log.append(
                run_id,
                "tool_call_started",
                call_id=call_id,
                step_name=agent_name,
                provider=self.model.settings.provider,
                operation="structured_completion",
                input_characters=input_size,
            )
        try:
            result = await self.model.complete(
                agent_name,
                system_prompt=system_prompt,
                input_payload=input_payload,
            )
        except Exception as exc:
            with self.database.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO tool_calls(
                        call_id,run_id,step_name,provider,operation,request_json,
                        response_summary_json,result_count,duration_ms,status,
                        error_code,error_message,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        call_id,
                        run_id,
                        agent_name,
                        self.model.settings.provider,
                        "structured_completion",
                        canonical_json({"agent": agent_name, "input_characters": input_size}),
                        None,
                        0,
                        None,
                        "ERROR",
                        type(exc).__name__,
                        str(exc)[:1000],
                        timestamp,
                    ),
                )
            if self.debug_log:
                self.debug_log.append(
                    run_id,
                    "tool_call_failed",
                    call_id=call_id,
                    step_name=agent_name,
                    operation="structured_completion",
                    error_code=type(exc).__name__,
                    error_message=str(exc)[:1000],
                )
            raise
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO tool_calls(
                    call_id,run_id,step_name,provider,operation,request_json,
                    response_summary_json,result_count,duration_ms,status,
                    error_code,error_message,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    call_id,
                    run_id,
                    agent_name,
                    result.model,
                    "structured_completion",
                    canonical_json({"agent": agent_name, "input_characters": input_size}),
                    canonical_json(
                        {
                            "attempts": result.attempts,
                            "usage": result.usage,
                            "response_id": result.response_id,
                        }
                    ),
                    1,
                    result.duration_ms,
                    "SUCCESS",
                    None,
                    None,
                    timestamp,
                ),
            )
        if self.debug_log:
            self.debug_log.append(
                run_id,
                "tool_call_completed",
                call_id=call_id,
                step_name=agent_name,
                provider=self.model.settings.provider,
                operation="structured_completion",
                model=result.model,
                attempts=result.attempts,
                duration_ms=result.duration_ms,
                usage=result.usage,
            )
        return result

    @staticmethod
    def _resolve_source_spans(
        idea_text: str, output: IdeaParserOutput
    ) -> IdeaParserOutput:
        resolved_features = []
        for feature in output.features:
            span = feature.source_span
            if span is None:
                resolved_features.append(feature)
                continue
            if not span.text:
                raise AgentExecutionError(f"feature span text is empty: {feature.feature_id}")
            if span.end <= len(idea_text) and idea_text[span.start : span.end] == span.text:
                resolved_features.append(feature)
                continue
            occurrences = []
            occurrence = idea_text.find(span.text)
            while occurrence >= 0:
                occurrences.append(occurrence)
                occurrence = idea_text.find(span.text, occurrence + 1)
            if not occurrences:
                raise AgentExecutionError(f"feature span does not match input: {feature.feature_id}")
            start = min(occurrences, key=lambda index: (abs(index - span.start), index))
            resolved_span = span.model_copy(
                update={"start": start, "end": start + len(span.text)}
            )
            resolved_features.append(feature.model_copy(update={"source_span": resolved_span}))
        return output.model_copy(update={"features": resolved_features})

    @staticmethod
    def _persist_stage_result(connection, run_id: str, stage_name: str, value: dict) -> None:
        encoded = canonical_json(value)
        connection.execute(
            "INSERT INTO stage_results VALUES(?,?,?,?,?)",
            (
                run_id,
                stage_name,
                encoded,
                hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
                now_ms(),
            ),
        )
