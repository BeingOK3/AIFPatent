from __future__ import annotations

import operator
from collections.abc import Sequence
from enum import StrEnum
from typing import Annotated, Any, NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from .execution import LandscapeExecutionService
from .workflow import LandscapeWorkflowHarness


class LandscapeTaskStep(StrEnum):
    ANALYZE_COMPANY = "ANALYZE_COMPANY"


class CompanyFanoutState(TypedDict):
    run_id: str
    company_ids: list[str]
    company_id: NotRequired[str]
    completed_company_ids: Annotated[list[str], operator.add]


class LandscapeCompanyFanout:
    """Run one durable company task per stable company ID."""

    def __init__(
        self,
        *,
        harness: LandscapeWorkflowHarness,
        execution: LandscapeExecutionService,
    ):
        self.harness = harness
        self.execution = execution
        self._graph = self._build_graph().compile(
            name="aifpatent-landscape-company-fanout"
        )

    async def execute(
        self,
        run_id: str,
        company_ids: Sequence[str],
    ) -> list[str]:
        stable_ids = sorted(set(company_ids))
        if len(stable_ids) != len(company_ids):
            raise ValueError("company fan-out IDs must be unique")
        if not stable_ids:
            return []
        result = await self._graph.ainvoke(
            {
                "run_id": run_id,
                "company_ids": stable_ids,
                "completed_company_ids": [],
            }
        )
        completed = sorted(set(result["completed_company_ids"]))
        if completed != stable_ids:
            raise RuntimeError("company fan-out did not complete every company")
        return completed

    def _build_graph(self) -> StateGraph:
        builder = StateGraph(CompanyFanoutState)
        builder.add_node("analyze_company", self._analyze_company)
        builder.add_conditional_edges(START, self._dispatch)
        builder.add_edge("analyze_company", END)
        return builder

    async def _dispatch(self, state: CompanyFanoutState):
        # Keep this router async. LangGraph 1.2.x otherwise uses a background
        # executor during ainvoke, which prevents short-lived event loops from
        # shutting down cleanly.
        return [
            Send(
                "analyze_company",
                {
                    "run_id": state["run_id"],
                    "company_id": company_id,
                    "completed_company_ids": [],
                },
            )
            for company_id in state["company_ids"]
        ]

    async def _analyze_company(
        self, state: CompanyFanoutState
    ) -> dict[str, Any]:
        run_id = state["run_id"]
        company_id = state["company_id"]
        step = LandscapeTaskStep.ANALYZE_COMPANY
        latest = self.harness.latest_task(
            run_id, step, task_key=company_id  # type: ignore[arg-type]
        )
        if latest is not None and latest["status"] == "SUCCEEDED":
            return {"completed_company_ids": [company_id]}
        attempt = self.harness.start_step(
            run_id,
            step,  # type: ignore[arg-type]
            {"company_id": company_id},
            task_key=company_id,
        )
        try:
            output = await self.execution.analyze_company(run_id, company_id)
        except Exception as exc:
            self.harness.fail_step(
                run_id,
                step,  # type: ignore[arg-type]
                attempt,
                exc,
                task_key=company_id,
            )
            raise
        self.harness.complete_step(
            run_id,
            step,  # type: ignore[arg-type]
            attempt,
            output,
            task_key=company_id,
        )
        return {"completed_company_ids": [company_id]}


__all__ = ["LandscapeCompanyFanout", "LandscapeTaskStep"]
