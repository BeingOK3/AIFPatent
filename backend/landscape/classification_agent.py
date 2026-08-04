from __future__ import annotations

import asyncio
import json

from pydantic import Field, model_validator

from idea.agent_schemas import register_agent_output_model

from .batch_executor import BatchValidationError, ResilientBatchExecutor
from .batching import BatchItem, BatchProfile, pack_items
from .classification_terminal import (
    ClassificationAction,
    ClassificationResult,
    ClassificationTerminal,
    validate_classification,
)
from .direction_record import DirectionRecord, DirectionStatus
from .model_scheduler import ModelScheduler
from .scope import ScopeModel
from .taxonomy import TaxonomyArtifact


CLASSIFICATION_AGENT_NAME = "landscape-v4-taxonomy-matcher"


_CLASSIFICATION_PROMPT = """
你是专利技术分类智能体。只根据输入的技术问题、解决机制、技术对象、场景和摘要证据，
从给定叶子分类中选择最具体的匹配项；申请人、日期和检索词不是分类依据。
EXACT_CATEGORY 表示存在明确匹配，NONE_OF_CANDIDATES 表示技术方向明确但候选均不匹配，
NEEDS_ALTERNATIVE_PARENT 表示技术方向明确但可能属于未提供的另一一级分类，UNRESOLVED
仅用于证据无法支持判断。只能引用输入 evidence_ids，不得编造分类 ID。每个输入单元必须
恰好返回一条 decision。
"""


class ClassificationDecision(ScopeModel):
    analysis_unit_id: str = Field(pattern=r"^AU-[0-9a-f]{16}$")
    action: ClassificationAction
    primary_category_id: str | None = None
    auxiliary_category_ids: tuple[str, ...] = Field(default=(), max_length=5)
    evidence_ids: tuple[str, ...] = Field(default=(), max_length=50)
    confidence: float = Field(ge=0, le=1)
    unresolved_reason: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def validate_action(self) -> "ClassificationDecision":
        if self.action == ClassificationAction.EXACT_CATEGORY:
            if not self.primary_category_id or self.unresolved_reason:
                raise ValueError("exact decision requires a category and no unresolved reason")
        elif self.primary_category_id is not None:
            raise ValueError("non-exact decision cannot have a primary category")
        if self.action == ClassificationAction.UNRESOLVED and not self.unresolved_reason:
            raise ValueError("unresolved decision requires a reason")
        if self.action != ClassificationAction.UNRESOLVED and self.unresolved_reason:
            raise ValueError("only unresolved decision may have an unresolved reason")
        if len(self.auxiliary_category_ids) != len(set(self.auxiliary_category_ids)):
            raise ValueError("duplicate auxiliary category")
        return self


class ClassificationBatchOutput(ScopeModel):
    decisions: tuple[ClassificationDecision, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_units(self) -> "ClassificationBatchOutput":
        identifiers = [item.analysis_unit_id for item in self.decisions]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("classification batch contains duplicate units")
        return self


class ClassificationMatchingService:
    def __init__(
        self,
        model,
        scheduler: ModelScheduler,
        profile: BatchProfile,
        *,
        batch_retries: int = 1,
    ):
        register_agent_output_model(CLASSIFICATION_AGENT_NAME, ClassificationBatchOutput)
        self.model = model
        self.scheduler = scheduler
        self.profile = profile
        self.batch_retries = batch_retries

    async def classify(
        self,
        directions: tuple[DirectionRecord, ...],
        taxonomy: TaxonomyArtifact,
    ) -> tuple[ClassificationResult, ...]:
        by_id = {item.analysis_unit_id: item for item in directions}
        if len(by_id) != len(directions):
            raise ValueError("directions contain duplicate analysis units")
        results: dict[str, ClassificationResult] = {}
        available = tuple(
            item for item in directions if item.status == DirectionStatus.AVAILABLE
        )
        for item in directions:
            if item.status == DirectionStatus.UNRESOLVED:
                results[item.analysis_unit_id] = ClassificationResult(
                    analysis_unit_id=item.analysis_unit_id,
                    action=ClassificationAction.UNRESOLVED,
                    terminal=ClassificationTerminal.UNRESOLVED,
                    confidence=0,
                    review_round=0,
                    unresolved_reason=item.unresolved_reason or "DIRECTION_UNRESOLVED",
                )
        leaf_parent_pairs = _leaf_parent_pairs(taxonomy)
        first_candidates = {
            item.analysis_unit_id: _candidate_leaves(
                item,
                taxonomy,
                leaf_parent_pairs,
                max_candidates=self.profile.max_taxonomy_candidates,
            )
            for item in available
        }
        first = await self._decide(available, first_candidates, taxonomy, review_round=0)
        needs_review = []
        for item in available:
            decision = first[item.analysis_unit_id]
            if decision.action == ClassificationAction.NEEDS_ALTERNATIVE_PARENT:
                needs_review.append(item)
            else:
                results[item.analysis_unit_id] = _terminal(
                    decision, item, taxonomy, review_round=0
                )
        if needs_review:
            all_leaves = tuple(taxonomy.leaf_category_ids)
            second_candidates = {
                item.analysis_unit_id: tuple(
                    category_id
                    for category_id in all_leaves
                    if category_id not in set(first_candidates[item.analysis_unit_id])
                )
                or all_leaves
                for item in needs_review
            }
            second_candidates = {
                item_id: _cap_candidates(
                    candidates,
                    self.profile.max_taxonomy_candidates,
                )
                for item_id, candidates in second_candidates.items()
            }
            second = await self._decide(
                tuple(needs_review), second_candidates, taxonomy, review_round=1
            )
            for item in needs_review:
                decision = second[item.analysis_unit_id]
                if decision.action == ClassificationAction.NEEDS_ALTERNATIVE_PARENT:
                    decision = ClassificationDecision(
                        analysis_unit_id=item.analysis_unit_id,
                        action=ClassificationAction.UNRESOLVED,
                        confidence=decision.confidence,
                        unresolved_reason="ALTERNATIVE_PARENT_REVIEW_EXHAUSTED",
                    )
                results[item.analysis_unit_id] = _terminal(
                    decision, item, taxonomy, review_round=1
                )
        if set(results) != set(by_id):
            raise RuntimeError("classification did not terminate every analysis unit")
        return tuple(results[item_id] for item_id in sorted(results))

    async def _decide(
        self,
        directions: tuple[DirectionRecord, ...],
        candidates_by_id: dict[str, tuple[str, ...]],
        taxonomy: TaxonomyArtifact,
        *,
        review_round: int,
    ) -> dict[str, ClassificationDecision]:
        by_id = {item.analysis_unit_id: item for item in directions}
        items = tuple(
            BatchItem(
                item_id=item.analysis_unit_id,
                input_tokens=_tokens(item.model_dump(mode="json")),
                output_tokens=400,
                taxonomy_candidates=len(candidates_by_id[item.analysis_unit_id]),
            )
            for item in directions
        )
        batches = pack_items(self.profile, items)
        node_by_id = {node.category_id: node for node in taxonomy.nodes}

        async def execute_batch(batch):
            executor = ResilientBatchExecutor(
                self.profile, batch_retries=self.batch_retries
            )

            async def operation(item_ids: tuple[str, ...]):
                payload = {
                    "review_round": review_round,
                    "units": [
                        {
                            "direction": by_id[item_id].model_dump(mode="json"),
                            "candidate_categories": [
                                {
                                    "category_id": category_id,
                                    "path": list(node_by_id[category_id].path),
                                }
                                for category_id in candidates_by_id[item_id]
                            ],
                        }
                        for item_id in item_ids
                    ],
                }

                async def call():
                    return await self.model.complete(
                        CLASSIFICATION_AGENT_NAME,
                        system_prompt=_CLASSIFICATION_PROMPT,
                        input_payload=payload,
                    )

                completion = await self.scheduler.run(
                    _tokens(payload),
                    min(self.profile.max_batch_output_tokens, 400 * len(item_ids)),
                    call,
                )
                output = ClassificationBatchOutput.model_validate(
                    completion.output.model_dump(mode="json")
                )
                response = {
                    decision.analysis_unit_id: decision
                    for decision in output.decisions
                }
                partial = {}
                for item_id, decision in response.items():
                    if item_id not in item_ids:
                        continue
                    allowed_categories = set(candidates_by_id[item_id])
                    cited = (
                        ({decision.primary_category_id} if decision.primary_category_id else set())
                        | set(decision.auxiliary_category_ids)
                    )
                    if not cited <= allowed_categories:
                        continue
                    if not set(decision.evidence_ids) <= set(by_id[item_id].evidence_ids):
                        continue
                    partial[item_id] = decision
                if set(partial) != set(item_ids):
                    raise BatchValidationError(
                        "classification response failed member or evidence validation",
                        partial=partial,
                    )
                return partial

            return await executor.execute(batch, operation)

        execution = await asyncio.gather(*(execute_batch(batch) for batch in batches))
        output = {
            item_id: value
            for result in execution
            for item_id, value in result.successes.items()
        }
        for result in execution:
            for item_id in result.unresolved:
                output[item_id] = ClassificationDecision(
                    analysis_unit_id=item_id,
                    action=ClassificationAction.UNRESOLVED,
                    confidence=0,
                    unresolved_reason="MODEL_BATCH_FAILED",
                )
        return output


def _leaf_parent_pairs(
    taxonomy: TaxonomyArtifact,
) -> tuple[tuple[str, str], ...]:
    """Pair every leaf with its level-1 ancestor id, preserving taxonomy order."""
    node_path_to_id = {node.path: node.category_id for node in taxonomy.nodes}
    return tuple(
        (node.category_id, node_path_to_id[node.path[:1]])
        for node in taxonomy.nodes
        if node.is_leaf
    )


def _candidate_leaves(
    direction: DirectionRecord,
    taxonomy: TaxonomyArtifact,
    leaf_parent_pairs: tuple[tuple[str, str], ...],
    *,
    max_candidates: int,
) -> tuple[str, ...]:
    if not direction.candidate_level1_ids:
        return _cap_candidates(
            tuple(taxonomy.leaf_category_ids),
            max_candidates,
        )
    parents = set(direction.candidate_level1_ids)
    return tuple(
        leaf_id
        for leaf_id, level1_id in leaf_parent_pairs
        if level1_id in parents
    )


def _cap_candidates(
    candidates: tuple[str, ...],
    max_candidates: int,
) -> tuple[str, ...]:
    if max_candidates < 1:
        raise ValueError("max_candidates must be positive")
    if len(candidates) <= max_candidates:
        return candidates
    # Deterministic bound so a single direction can never push a batch item
    # past the frozen batch profile. The bounded parent-review round still
    # offers the remaining leaves, so no category is silently unreachable.
    return candidates[:max_candidates]


def _terminal(
    decision: ClassificationDecision,
    direction: DirectionRecord,
    taxonomy: TaxonomyArtifact,
    *,
    review_round: int,
) -> ClassificationResult:
    terminal = (
        ClassificationTerminal.CLASSIFIED
        if decision.action == ClassificationAction.EXACT_CATEGORY
        else ClassificationTerminal.OTHERS
        if decision.action == ClassificationAction.NONE_OF_CANDIDATES
        else ClassificationTerminal.UNRESOLVED
    )
    unresolved_reason = (
        decision.unresolved_reason or decision.action.value
        if terminal == ClassificationTerminal.UNRESOLVED
        else None
    )
    result = ClassificationResult(
        analysis_unit_id=decision.analysis_unit_id,
        action=(
            decision.action
            if terminal != ClassificationTerminal.UNRESOLVED
            else ClassificationAction.UNRESOLVED
        ),
        terminal=terminal,
        primary_category_id=decision.primary_category_id,
        auxiliary_category_ids=decision.auxiliary_category_ids,
        evidence_ids=decision.evidence_ids,
        confidence=decision.confidence,
        review_round=review_round,
        unresolved_reason=unresolved_reason,
    )
    return validate_classification(
        result,
        expected_analysis_unit_id=direction.analysis_unit_id,
        allowed_evidence_ids=set(direction.evidence_ids),
        taxonomy=taxonomy,
    )


def _tokens(value: object) -> int:
    size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    return max(1, (size + 1) // 2)


__all__ = [
    "CLASSIFICATION_AGENT_NAME",
    "ClassificationBatchOutput",
    "ClassificationDecision",
    "ClassificationMatchingService",
]
