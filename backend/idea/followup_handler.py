from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Sequence

from .agent_schemas import IdeaFeature
from .context import AssembledModelContext
from .followup import FollowupError, FollowupTurn
from .followup_answer import (
    FollowupAnswer,
    FollowupAnswerVerifier,
    VerifiedFollowupAnswer,
)
from .followup_context import FollowupContextBuilder, PreparedFollowupContext
from .followup_plan import FollowupPlan, FollowupPlanVerifier
from .followup_workflow import FollowupWorkflowStep
from .hybrid import HybridSearchResult


class FollowupBusinessRepository(Protocol):
    async def get_turn(self, turn_id: str) -> FollowupTurn | None: ...

    async def get_thread(self, thread_id: str): ...

    async def save_plan(self, turn_id: str, plan: dict) -> FollowupTurn: ...

    async def record_retrieval(
        self,
        turn_id: str,
        result: HybridSearchResult,
        *,
        selected_chunk_ids: Sequence[str] | None = None,
    ) -> int: ...

    async def record_citations(self, turn_id: str, drafts: Sequence): ...

    async def complete_turn(
        self, turn_id: str, *, answer: dict, limitations: Sequence[str] = ()
    ) -> FollowupTurn: ...


@dataclass(frozen=True)
class FollowupSourceData:
    features: tuple[IdeaFeature, ...]
    recent_turns: tuple[FollowupTurn, ...] = ()
    report_summary: str = ""
    report_limitations: tuple[str, ...] = ()


class FollowupDataSource(Protocol):
    async def load(self, *, run_id: str, turn: FollowupTurn) -> FollowupSourceData: ...


class FollowupPlanningModel(Protocol):
    async def plan(
        self, *, turn: FollowupTurn, source: FollowupSourceData
    ) -> FollowupPlan: ...

    async def answer(
        self,
        *,
        turn: FollowupTurn,
        plan: FollowupPlan,
        context: AssembledModelContext,
    ) -> FollowupAnswer: ...


class FollowupEvidenceRetriever(Protocol):
    async def retrieve(
        self, *, turn: FollowupTurn, plan: FollowupPlan
    ) -> HybridSearchResult: ...


class FollowupContextRepository(Protocol):
    async def put_if_absent(
        self, context: AssembledModelContext, *, agent_name: str
    ) -> str: ...


@dataclass
class _TurnMemory:
    run_id: str
    turn: FollowupTurn
    source: FollowupSourceData
    plan: FollowupPlan | None = None
    retrieval: HybridSearchResult | None = None
    prepared: PreparedFollowupContext | None = None
    answer: FollowupAnswer | None = None
    verified: VerifiedFollowupAnswer | None = None
    limitations: list[str] = field(default_factory=list)


class FollowupBusinessHandler:
    """Concrete fixed-node business handler; BYOK remains outside durable state."""

    def __init__(
        self,
        *,
        repository: FollowupBusinessRepository,
        data_source: FollowupDataSource,
        model: FollowupPlanningModel,
        retriever: FollowupEvidenceRetriever,
        context_builder: FollowupContextBuilder,
        context_repository: FollowupContextRepository,
        system_prompt: str,
        input_budget: int,
        reserved_output_tokens: int,
        plan_verifier: FollowupPlanVerifier | None = None,
        answer_verifier: FollowupAnswerVerifier | None = None,
    ) -> None:
        if not system_prompt.strip() or input_budget < 1 or reserved_output_tokens < 0:
            raise ValueError("follow-up Handler requires a Prompt and usable budgets")
        self.repository = repository
        self.data_source = data_source
        self.model = model
        self.retriever = retriever
        self.context_builder = context_builder
        self.context_repository = context_repository
        self.system_prompt = system_prompt
        self.input_budget = input_budget
        self.reserved_output_tokens = reserved_output_tokens
        self.plan_verifier = plan_verifier or FollowupPlanVerifier()
        self.answer_verifier = answer_verifier or FollowupAnswerVerifier()
        self._memory: dict[str, _TurnMemory] = {}

    async def execute(
        self, turn_id: str, step: FollowupWorkflowStep, attempt: int
    ) -> None:
        if attempt < 1:
            raise ValueError("follow-up step attempt must be positive")
        handlers = {
            FollowupWorkflowStep.PREPARE_FOLLOWUP_SCOPE: self._prepare,
            FollowupWorkflowStep.CLASSIFY_AND_PLAN: self._plan,
            FollowupWorkflowStep.RETRIEVE_FOLLOWUP_EVIDENCE: self._retrieve,
            FollowupWorkflowStep.ASSEMBLE_FOLLOWUP_CONTEXT: self._assemble,
            FollowupWorkflowStep.GENERATE_FOLLOWUP_ANSWER: self._generate,
            FollowupWorkflowStep.VERIFY_FOLLOWUP_ANSWER: self._verify,
            FollowupWorkflowStep.PERSIST_FOLLOWUP_RESPONSE: self._persist,
        }
        await handlers[step](turn_id)

    def discard(self, turn_id: str) -> None:
        self._memory.pop(turn_id, None)

    async def _prepare(self, turn_id: str) -> None:
        turn = await self.repository.get_turn(turn_id)
        if turn is None or turn.status.value != "RUNNING":
            raise FollowupError("business Handler requires a RUNNING Turn")
        thread = await self.repository.get_thread(turn.thread_id)
        if thread is None:
            raise FollowupError("business Handler requires the source Thread")
        source = await self.data_source.load(run_id=thread.run_id, turn=turn)
        if not source.features:
            raise FollowupError("source Run has no IDEA Features for follow-up")
        self._memory[turn_id] = _TurnMemory(
            run_id=thread.run_id, turn=turn, source=source
        )

    async def _plan(self, turn_id: str) -> None:
        memory = self._required(turn_id)
        candidate = await self.model.plan(turn=memory.turn, source=memory.source)
        memory.plan = self.plan_verifier.verify(
            candidate,
            turn=memory.turn,
            allowed_feature_ids=(item.feature_id for item in memory.source.features),
        )
        await self.repository.save_plan(
            turn_id, memory.plan.model_dump(mode="json")
        )

    async def _retrieve(self, turn_id: str) -> None:
        memory = self._required(turn_id, "plan")
        memory.retrieval = await self.retriever.retrieve(
            turn=memory.turn, plan=memory.plan
        )
        if memory.retrieval.retriever_version != memory.turn.retriever_version:
            raise FollowupError("business Retriever version does not match the Turn")
        memory.limitations.extend(memory.retrieval.limitations)

    async def _assemble(self, turn_id: str) -> None:
        memory = self._required(turn_id, "plan", "retrieval")
        memory.prepared = self.context_builder.build(
            run_id=memory.run_id,
            turn=memory.turn,
            features=memory.source.features,
            recent_turns=memory.source.recent_turns,
            retrieval=memory.retrieval,
            system_prompt=self.system_prompt,
            source_report_summary=memory.source.report_summary,
            source_report_limitations=memory.source.report_limitations,
            selected_publication_numbers=memory.plan.selected_publication_numbers,
            input_budget=self.input_budget,
            reserved_output_tokens=self.reserved_output_tokens,
        )
        await self.repository.record_retrieval(
            turn_id,
            memory.retrieval,
            selected_chunk_ids=memory.prepared.selected_chunk_ids,
        )
        persisted = await self.context_repository.put_if_absent(
            memory.prepared.context, agent_name="patent-followup-answerer"
        )
        if persisted != memory.prepared.context.context_id:
            raise FollowupError("follow-up Context repository returned a different ID")
        memory.limitations.extend(memory.prepared.context.limitations)

    async def _generate(self, turn_id: str) -> None:
        memory = self._required(turn_id, "plan", "prepared")
        memory.answer = await self.model.answer(
            turn=memory.turn,
            plan=memory.plan,
            context=memory.prepared.context,
        )

    async def _verify(self, turn_id: str) -> None:
        memory = self._required(turn_id, "prepared", "answer")
        memory.verified = self.answer_verifier.verify(
            memory.answer,
            context=memory.prepared.context,
            allowed_feature_ids=memory.prepared.feature_ids,
            allowed_publication_numbers=memory.prepared.publication_numbers,
        )

    async def _persist(self, turn_id: str) -> None:
        memory = self._required(turn_id, "verified")
        await self.repository.record_citations(turn_id, memory.verified.citations)
        limitations = tuple(dict.fromkeys(
            [*memory.limitations, *memory.verified.answer.limitations]
        ))
        await self.repository.complete_turn(
            turn_id,
            answer=memory.verified.answer.model_dump(mode="json"),
            limitations=limitations,
        )

    def _required(self, turn_id: str, *fields: str) -> _TurnMemory:
        memory = self._memory.get(turn_id)
        if memory is None:
            raise FollowupError("follow-up ephemeral state is unavailable after restart")
        missing = [name for name in fields if getattr(memory, name) is None]
        if missing:
            raise FollowupError(
                "follow-up step prerequisites are missing: " + ", ".join(missing)
            )
        return memory


__all__ = [
    "FollowupBusinessHandler",
    "FollowupDataSource",
    "FollowupEvidenceRetriever",
    "FollowupPlanningModel",
    "FollowupSourceData",
]
