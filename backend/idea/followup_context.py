from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Sequence

from .agent_schemas import IdeaFeature
from .context import AssembledModelContext, ContextAssembler, ContextNote
from .followup import FollowupError, FollowupTurn, TurnStatus
from .hybrid import HybridSearchResult


@dataclass(frozen=True)
class PreparedFollowupContext:
    context: AssembledModelContext
    feature_ids: tuple[str, ...]
    publication_numbers: tuple[str, ...]
    history_turn_ids: tuple[str, ...]
    selected_chunk_ids: tuple[str, ...]


class FollowupContextBuilder:
    """Build one evidence-first follow-up Context from frozen domain objects."""

    def __init__(self, assembler: ContextAssembler, *, history_limit: int = 5) -> None:
        if not 1 <= history_limit <= 5:
            raise ValueError("follow-up history limit must be between 1 and 5")
        self.assembler = assembler
        self.history_limit = history_limit

    def build(
        self,
        *,
        run_id: str,
        turn: FollowupTurn,
        features: Sequence[IdeaFeature],
        recent_turns: Sequence[FollowupTurn],
        retrieval: HybridSearchResult,
        system_prompt: str,
        source_report_summary: str = "",
        source_report_limitations: Sequence[str] = (),
        selected_publication_numbers: Sequence[str] | None = None,
        input_budget: int,
        reserved_output_tokens: int,
    ) -> PreparedFollowupContext:
        if turn.status != TurnStatus.RUNNING:
            raise FollowupError("follow-up Context requires a RUNNING Turn")
        if retrieval.retriever_version != turn.retriever_version:
            raise FollowupError("follow-up Context Retriever version does not match the Turn")
        if not retrieval.hits:
            raise FollowupError("follow-up Context requires fresh retrieval evidence")

        feature_ids = tuple(feature.feature_id for feature in features)
        if not feature_ids or len(set(feature_ids)) != len(feature_ids):
            raise FollowupError("follow-up Context requires unique IDEA Features")

        requested_publications = tuple(
            turn.scope.publication_numbers
            if selected_publication_numbers is None
            else selected_publication_numbers
        )
        if not requested_publications or len(set(requested_publications)) != len(
            requested_publications
        ):
            raise FollowupError("follow-up Context publication selection must be non-empty and unique")
        unknown_publications = set(requested_publications) - set(
            turn.scope.publication_numbers
        )
        if unknown_publications:
            raise FollowupError("follow-up Context publications escaped the frozen Turn scope")
        selected_documents = tuple(
            item for item in turn.scope.documents
            if item.publication_number in requested_publications
        )
        scope_by_version = {
            document.version_id: document.publication_number
            for document in selected_documents
        }
        chunk_ids: list[str] = []
        for hit in retrieval.hits:
            chunk = hit.chunk
            if scope_by_version.get(chunk.version_id) != chunk.publication_number:
                raise FollowupError("follow-up Context evidence escaped the frozen Turn scope")
            if chunk.chunk_id in chunk_ids:
                raise FollowupError("follow-up Context evidence contains duplicate Chunks")
            chunk_ids.append(chunk.chunk_id)

        ordered_history = self._history(turn, recent_turns)
        omitted_history = max(0, len(ordered_history) - self.history_limit)
        selected_history = ordered_history[-self.history_limit :]

        notes = [
            ContextNote.create(
                note_id=turn.turn_id,
                kind="FROZEN_SCOPE",
                content=self._json({
                    "corpus_snapshot_hash": turn.scope.corpus_snapshot_hash,
                    "documents": [
                        {
                            "publication_number": item.publication_number,
                            "version_id": item.version_id,
                        }
                        for item in selected_documents
                    ],
                }),
            ),
            ContextNote.create(
                note_id=run_id,
                kind="IDEA_FEATURES",
                content=self._json([
                    {
                        "feature_id": feature.feature_id,
                        "feature_text": feature.feature_text,
                        "required": feature.required,
                    }
                    for feature in features
                ]),
            ),
        ]
        summary = source_report_summary.strip()
        limitations = tuple(
            dict.fromkeys(value.strip() for value in source_report_limitations if value.strip())
        )
        if summary or limitations:
            notes.append(ContextNote.create(
                note_id=run_id,
                kind="SOURCE_REPORT",
                content=self._json({"summary": summary, "limitations": list(limitations)}),
            ))
        notes.extend(
            ContextNote.create(
                note_id=item.turn_id,
                kind="RECENT_TURN",
                content=self._json({
                    "question": item.question_text,
                    "answer": item.answer,
                    "limitations": list(item.limitations),
                }),
            )
            for item in selected_history
        )

        context_limitations = list(retrieval.limitations)
        if omitted_history:
            context_limitations.append("HISTORY_WINDOW_LIMIT")
        context = self.assembler.assemble(
            purpose="FOLLOWUP",
            run_id=run_id,
            turn_id=turn.turn_id,
            corpus_snapshot_hash=turn.scope.corpus_snapshot_hash,
            chunks=tuple(hit.chunk for hit in retrieval.hits),
            notes=tuple(notes),
            system_prompt=system_prompt,
            question=turn.question_text,
            prompt_version=turn.prompt_version,
            retriever_version=turn.retriever_version,
            input_budget=input_budget,
            reserved_output_tokens=reserved_output_tokens,
            additional_limitations=tuple(context_limitations),
            allowed_version_ids=tuple(item.version_id for item in selected_documents),
        )
        selected_chunk_ids = tuple(
            str(item["chunk_id"]) for item in context.selected_chunks
        )
        return PreparedFollowupContext(
            context=context,
            feature_ids=feature_ids,
            publication_numbers=tuple(item.publication_number for item in selected_documents),
            history_turn_ids=tuple(item.turn_id for item in selected_history),
            selected_chunk_ids=selected_chunk_ids,
        )

    @staticmethod
    def _history(
        current: FollowupTurn, values: Sequence[FollowupTurn]
    ) -> tuple[FollowupTurn, ...]:
        if len({item.turn_id for item in values}) != len(values):
            raise FollowupError("follow-up history contains duplicate Turns")
        for item in values:
            if item.turn_id == current.turn_id or item.thread_id != current.thread_id:
                raise FollowupError("follow-up history escaped the current Thread")
            if item.status not in {
                TurnStatus.COMPLETED,
                TurnStatus.COMPLETED_WITH_LIMITATIONS,
            } or not isinstance(item.answer, dict) or not item.answer:
                raise FollowupError("follow-up history must contain completed answered Turns")
            if item.created_at >= current.created_at:
                raise FollowupError("follow-up history cannot include the current or future Turns")
        return tuple(sorted(values, key=lambda item: (item.created_at, item.turn_id)))

    @staticmethod
    def _json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


__all__ = ["FollowupContextBuilder", "PreparedFollowupContext"]
