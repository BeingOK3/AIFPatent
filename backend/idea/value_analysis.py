from __future__ import annotations

from .agent_schemas import (
    IdeaParserOutput,
    InventiveStepOutput,
    NoveltyResult,
    ValueAnalyzerOutput,
)
from .agents import AgentExecutionError, IdeaAgentService, PLACEHOLDER_PATTERN
from .database import Database, canonical_json, now_ms


VALUE_ANALYZER_PROMPT = """
You are patent-value-analyzer. Perform a preliminary filing-value assessment from only the
supplied frozen IDEA summary, novelty result, and inventive-step route summaries. Assess
detectability, workaround difficulty, and technical/market value; give at least two concrete
alternative paths and a filing recommendation. Do not change or reinterpret the supplied
novelty/inventive conclusions, do not search, and do not invent patent or market facts. Every
evidence_basis entry must be one of the supplied basis IDs. State uncertainty in limitations.
Score every dimension with an integer from 1 to 5, where 1 is very low, 2 is low, 3 is medium,
4 is high, and 5 is very high. A higher workaround-difficulty score means the protected design
is harder for a competitor to avoid. Write every rationale, alternative path, recommendation
explanation, and limitation in Simplified Chinese.
"""


class ValueAnalysisService:
    def __init__(self, database: Database, agents: IdeaAgentService):
        self.database = database
        self.agents = agents

    async def analyze(
        self,
        run_id: str,
        idea: IdeaParserOutput,
        novelty: NoveltyResult,
        inventive_routes: list[InventiveStepOutput],
    ) -> ValueAnalyzerOutput:
        basis = self._basis(idea, novelty, inventive_routes)
        payload = {
            "idea": {
                "title": idea.title,
                "technical_domains": idea.technical_domains,
                "application_scenario": idea.application_scenario,
                "features": [
                    {
                        "basis_id": f"IDEA:{feature.feature_id}",
                        "feature_id": feature.feature_id,
                        "feature_text": feature.feature_text,
                    }
                    for feature in idea.features
                ],
                "claimed_effects": [
                    {"basis_id": f"EFFECT:{index}", "text": effect}
                    for index, effect in enumerate(idea.claimed_effects, 1)
                ],
            },
            "novelty": {
                "basis_id": "NOVELTY:CONCLUSION",
                "conclusion": novelty.conclusion,
                "confidence": novelty.confidence,
                "closest_publication_number": novelty.closest_publication_number,
                "missing_features": novelty.missing_features,
                "rationale": novelty.rationale,
                "limitations": novelty.limitations,
            },
            "inventive_routes": [
                {
                    "basis_id": f"INVENTIVE:{route.route_id}",
                    "route_id": route.route_id,
                    "d1_publication_number": route.d1_publication_number,
                    "status": route.status,
                    "distinguishing_feature_ids": [
                        item.feature_id for item in route.distinguishing_features
                    ],
                    "objective_technical_problem": route.objective_technical_problem,
                    "rationale": route.overall_rationale,
                    "limitations": route.limitations,
                }
                for route in inventive_routes
            ],
            "allowed_basis_ids": sorted(basis),
        }
        result = await self.agents.call_agent(
            run_id,
            "patent-value-analyzer",
            system_prompt=VALUE_ANALYZER_PROMPT,
            input_payload=payload,
            input_size=len(canonical_json(payload)),
        )
        output = result.output
        if not isinstance(output, ValueAnalyzerOutput):
            raise AgentExecutionError("value analyzer returned wrong validated model")
        cited_basis = {
            basis_id
            for dimension in (
                output.detectability,
                output.workaround_difficulty,
                output.technical_market_value,
            )
            for basis_id in dimension.evidence_basis
        }
        if any(
            not dimension.evidence_basis
            for dimension in (
                output.detectability,
                output.workaround_difficulty,
                output.technical_market_value,
            )
        ):
            raise AgentExecutionError("every value dimension requires a supplied basis ID")
        unknown = cited_basis - basis
        if unknown:
            raise AgentExecutionError(f"value analyzer cited unknown basis IDs: {sorted(unknown)}")
        if any(PLACEHOLDER_PATTERN.search(path) for path in output.alternative_paths):
            raise AgentExecutionError("value analyzer returned a placeholder alternative path")
        self._persist(run_id, output)
        return output

    @staticmethod
    def _basis(
        idea: IdeaParserOutput,
        novelty: NoveltyResult,
        inventive_routes: list[InventiveStepOutput],
    ) -> set[str]:
        basis = {f"IDEA:{feature.feature_id}" for feature in idea.features}
        basis.update(f"EFFECT:{index}" for index, _ in enumerate(idea.claimed_effects, 1))
        basis.add("NOVELTY:CONCLUSION")
        basis.update(f"INVENTIVE:{route.route_id}" for route in inventive_routes)
        return basis

    def _persist(self, run_id: str, output: ValueAnalyzerOutput) -> None:
        with self.database.connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM value_results WHERE run_id = ?", (run_id,)
            ).fetchone()
            if exists:
                raise AgentExecutionError("value result already exists for this run")
            connection.execute(
                "INSERT INTO value_results(run_id,result_json,created_at) VALUES(?,?,?)",
                (run_id, canonical_json(output.model_dump(mode="json")), now_ms()),
            )
