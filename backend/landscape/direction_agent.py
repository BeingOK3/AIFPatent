from __future__ import annotations

import asyncio
import json

from pydantic import Field, model_validator

from idea.agent_schemas import register_agent_output_model

from .batch_executor import BatchValidationError, ResilientBatchExecutor
from .batching import BatchItem, BatchProfile, pack_items
from .direction_record import (
    DirectionRecord,
    DirectionStatus,
    validate_direction_record,
)
from .model_scheduler import ModelScheduler
from .scope import ScopeModel
from .taxonomy import TaxonomyArtifact


DIRECTION_AGENT_NAME = "landscape-v4-direction-extractor"


_DIRECTION_PROMPT = """
你是专利技术方向提取智能体。输入只包含标题和摘要证据，不包含权利要求或说明书。
逐个分析发明单元，判断现有证据能否具体说明技术问题、解决机制和技术对象。
证据足够时输出 AVAILABLE，用一段简体中文 direction_summary 和精炼关键词描述具体
技术所指；只能引用该单元给出的 evidence_ids。证据不足时主动输出 UNRESOLVED，说明
原因，不得猜测。candidate_level1_ids 只能从给定一级分类中选择，它只是下一步分类的
候选，不是最终分类。必须为每个输入 analysis_unit_id 恰好输出一条记录。
"""


class DirectionUnitPacket(ScopeModel):
    analysis_unit_id: str = Field(pattern=r"^AU-[0-9a-f]{16}$")
    patent_abstracts: tuple[str, ...] = Field(min_length=1, max_length=100)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=100)


class DirectionBatchOutput(ScopeModel):
    records: tuple[DirectionRecord, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_units(self) -> "DirectionBatchOutput":
        identifiers = [item.analysis_unit_id for item in self.records]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("direction batch contains duplicate analysis units")
        return self


class DirectionExtractionService:
    def __init__(
        self,
        model,
        scheduler: ModelScheduler,
        profile: BatchProfile,
        *,
        batch_retries: int = 1,
    ):
        register_agent_output_model(DIRECTION_AGENT_NAME, DirectionBatchOutput)
        self.model = model
        self.scheduler = scheduler
        self.profile = profile
        self.batch_retries = batch_retries

    async def extract(
        self,
        packets: tuple[DirectionUnitPacket, ...],
        taxonomy: TaxonomyArtifact,
    ) -> tuple[DirectionRecord, ...]:
        by_id = {packet.analysis_unit_id: packet for packet in packets}
        if len(by_id) != len(packets):
            raise ValueError("direction packets contain duplicate analysis units")
        items = tuple(
            BatchItem(
                item_id=packet.analysis_unit_id,
                input_tokens=_estimate_tokens(packet.model_dump(mode="json")),
                output_tokens=700,
                taxonomy_candidates=len(
                    [node for node in taxonomy.nodes if node.level == 1]
                ),
            )
            for packet in packets
        )
        batches = pack_items(self.profile, items)

        async def execute_batch(batch):
            executor = ResilientBatchExecutor(
                self.profile, batch_retries=self.batch_retries
            )

            async def operation(item_ids: tuple[str, ...]) -> dict[str, DirectionRecord]:
                selected = tuple(by_id[item_id] for item_id in item_ids)
                input_payload = {
                    "units": [item.model_dump(mode="json") for item in selected],
                    "level1_categories": [
                        {
                            "category_id": node.category_id,
                            "name": node.name,
                        }
                        for node in taxonomy.nodes
                        if node.level == 1
                    ],
                }
                input_tokens = _estimate_tokens(input_payload)

                async def call():
                    return await self.model.complete(
                        DIRECTION_AGENT_NAME,
                        system_prompt=_DIRECTION_PROMPT,
                        input_payload=input_payload,
                    )

                completion = await self.scheduler.run(
                    input_tokens,
                    min(self.profile.max_batch_output_tokens, 700 * len(selected)),
                    call,
                )
                output = DirectionBatchOutput.model_validate(
                    completion.output.model_dump(mode="json")
                )
                response = {record.analysis_unit_id: record for record in output.records}
                partial: dict[str, DirectionRecord] = {}
                for item_id, record in response.items():
                    if item_id not in item_ids:
                        continue
                    packet = by_id[item_id]
                    partial[item_id] = validate_direction_record(
                        record,
                        expected_analysis_unit_id=item_id,
                        allowed_evidence_ids=set(packet.evidence_ids),
                        taxonomy=taxonomy,
                    )
                if set(response) != set(item_ids):
                    raise BatchValidationError(
                        "direction response members do not match request",
                        partial=partial,
                    )
                return partial

            return await executor.execute(batch, operation)

        results = await asyncio.gather(*(execute_batch(batch) for batch in batches))
        successes = {
            item_id: value
            for result in results
            for item_id, value in result.successes.items()
        }
        unresolved = {
            item_id for result in results for item_id in result.unresolved
        }
        records = [successes[item_id] for item_id in sorted(successes)]
        records.extend(
            DirectionRecord(
                analysis_unit_id=item_id,
                status=DirectionStatus.UNRESOLVED,
                evidence_sufficient=False,
                confidence=0,
                unresolved_reason="MODEL_BATCH_FAILED",
            )
            for item_id in sorted(unresolved)
        )
        if {record.analysis_unit_id for record in records} != set(by_id):
            raise RuntimeError("direction extraction did not terminate every unit")
        return tuple(sorted(records, key=lambda item: item.analysis_unit_id))


def _estimate_tokens(value: object) -> int:
    # Conservative local estimator; the scheduler remains the hard request and
    # concurrency gate. CJK-heavy JSON is intentionally charged more than 1/4.
    size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    return max(1, (size + 1) // 2)


__all__ = [
    "DIRECTION_AGENT_NAME",
    "DirectionBatchOutput",
    "DirectionExtractionService",
    "DirectionUnitPacket",
]
