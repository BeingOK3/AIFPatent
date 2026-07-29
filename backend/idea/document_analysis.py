from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

from .agent_schemas import DocumentAnalyzerOutput, IdeaParserOutput
from .agents import AgentExecutionError, IdeaAgentService
from .citations import ModelCitationSelection
from .context import AssembledModelContext
from .database import Database, canonical_json, now_ms
from .providers import FetchedDocument


DOCUMENT_ANALYZER_PROMPT = """
You are patent-document-analyzer. Analyze exactly one patent against the supplied F1..Fn.
Use only the supplied evidence packet. Every DISCLOSED or PARTIAL mapping must cite one or more
provided evidence aliases (E1, E2, ...); never create an alias. Map every required feature exactly
once. Explain the
document's technical problem, solution, effect, scenario and independent claim in plain language.
Copy the supplied publication_number exactly into the output; it is an identifier, not content to infer.
Do not decide overall novelty or combine this document with another document.
"""


@dataclass(frozen=True)
class EvidencePacketItem:
    evidence_id: str
    section_type: str
    section_label: str
    text: str
    start_offset: int | None
    end_offset: int | None
    score: float


class DocumentAnalysisService:
    def __init__(
        self,
        database: Database,
        agents: IdeaAgentService,
        *,
        concurrency: int = 3,
        max_packet_characters: int = 40_000,
        max_description_spans: int = 12,
        citations: Any | None = None,
    ):
        self.database = database
        self.agents = agents
        self.concurrency = concurrency
        self.max_packet_characters = max_packet_characters
        self.max_description_spans = max_description_spans
        self.citations = citations

    async def analyze_many_rag(
        self,
        *,
        run_id: str,
        idea: IdeaParserOutput,
        documents: list[FetchedDocument],
        document_ids: dict[str, str],
        contexts: tuple[AssembledModelContext, ...],
    ) -> dict[str, DocumentAnalyzerOutput]:
        if self.citations is None:
            raise AgentExecutionError("RAG document analysis requires a Citation repository")
        contexts_by_publication: dict[str, AssembledModelContext] = {}
        for context in contexts:
            publications = {
                str(item["publication_number"]) for item in context.selected_chunks
            }
            if context.run_id != run_id or context.purpose != "INITIAL_REVIEW":
                raise AgentExecutionError("RAG Context escaped its Run or purpose")
            if len(publications) != 1:
                raise AgentExecutionError("RAG Context must contain exactly one patent")
            publication = next(iter(publications))
            if publication in contexts_by_publication:
                raise AgentExecutionError("RAG Context publication is duplicated")
            contexts_by_publication[publication] = context
        expected_publications = {item.publication_number for item in documents}
        if not expected_publications.issubset(contexts_by_publication):
            raise AgentExecutionError("RAG Contexts do not cover every pending document")

        semaphore = asyncio.Semaphore(self.concurrency)

        async def analyze(document: FetchedDocument):
            async with semaphore:
                output = await self.analyze_rag(
                    run_id=run_id,
                    idea=idea,
                    document=document,
                    document_id=document_ids[document.publication_number],
                    context=contexts_by_publication[document.publication_number],
                )
                return document.publication_number, output

        tasks = [asyncio.create_task(analyze(document)) for document in documents]
        try:
            results = await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        return dict(results)

    async def analyze_rag(
        self,
        *,
        run_id: str,
        idea: IdeaParserOutput,
        document: FetchedDocument,
        document_id: str,
        context: AssembledModelContext,
    ) -> DocumentAnalyzerOutput:
        if self.citations is None:
            raise AgentExecutionError("RAG document analysis requires a Citation repository")
        if len(context.messages) != 2:
            raise AgentExecutionError("RAG Context must contain system and user messages")
        aliases: dict[str, str] = {}
        chunks_by_alias: dict[str, str] = {}
        packet: list[EvidencePacketItem] = []
        for binding in context.selected_chunks:
            alias = str(binding["alias"])
            chunk_id = str(binding["chunk_id"])
            if alias in aliases:
                raise AgentExecutionError("RAG Context contains a duplicate Citation alias")
            durable_id = "E-" + hashlib.sha256(
                f"{run_id}|{document_id}|{chunk_id}".encode("utf-8")
            ).hexdigest()[:20]
            aliases[alias] = durable_id
            chunks_by_alias[alias] = chunk_id
            packet.append(
                EvidencePacketItem(
                    evidence_id=durable_id,
                    section_type=str(binding["section_type"]),
                    section_label=str(binding["section_label"]),
                    text=str(binding["excerpt"]),
                    start_offset=int(binding["start_offset"]),
                    end_offset=int(binding["end_offset"]),
                    score=0.0,
                )
            )
        if not packet:
            raise AgentExecutionError("RAG Context contains no evidence")
        self._persist_evidence(run_id, document_id, packet)
        result = await self.agents.call_agent(
            run_id,
            "patent-document-analyzer",
            system_prompt=context.messages[0].content,
            input_payload={
                "publication_number": document.publication_number,
                "metadata": {
                    "title": document.title,
                    "assignee": document.assignee,
                    "priority_date": document.priority_date,
                    "publication_date": document.publication_date,
                },
                "features": [
                    {
                        "feature_id": feature.feature_id,
                        "feature_text": feature.feature_text,
                        "required": feature.required,
                    }
                    for feature in idea.features
                ],
                "context_id": context.context_id,
                "assembled_context": context.messages[1].content,
            },
            input_size=sum(len(message.content) for message in context.messages),
        )
        output = result.output
        if not isinstance(output, DocumentAnalyzerOutput):
            raise AgentExecutionError("document analyzer returned wrong validated model")
        self._validate_output_identity_and_features(output, idea, document)
        selections: list[ModelCitationSelection] = []
        for mapping in output.feature_mappings:
            if mapping.status in {"NOT_DISCLOSED", "UNCERTAIN"} and mapping.evidence_ids:
                raise AgentExecutionError(
                    f"{mapping.status} mapping must not cite evidence: {mapping.feature_id}"
                )
            for alias in mapping.evidence_ids:
                if re.fullmatch(r"C[1-9][0-9]*", alias) is None or alias not in aliases:
                    raise AgentExecutionError(
                        f"document analyzer cited unknown Context alias: {alias}"
                    )
                selections.append(
                    ModelCitationSelection(
                        feature_id=mapping.feature_id,
                        alias=alias,
                        chunk_id=chunks_by_alias[alias],
                    )
                )
        await self.citations.record(
            run_id=run_id,
            document_id=document_id,
            context=context,
            selections=tuple(selections),
        )
        resolved = self._resolve_evidence_aliases(output, aliases)
        if self._identifier(resolved.publication_number) != self._identifier(
            document.publication_number
        ):
            resolved = resolved.model_copy(
                update={"publication_number": document.publication_number}
            )
        self._persist_analysis(run_id, document_id, resolved)
        self._release_rebuildable_text(document_id)
        return resolved

    async def analyze_many(
        self,
        *,
        run_id: str,
        idea: IdeaParserOutput,
        documents: list[FetchedDocument],
        document_ids: dict[str, str],
    ) -> dict[str, DocumentAnalyzerOutput]:
        semaphore = asyncio.Semaphore(self.concurrency)

        async def analyze(document: FetchedDocument):
            async with semaphore:
                output = await self.analyze(
                    run_id=run_id,
                    idea=idea,
                    document=document,
                    document_id=document_ids[document.publication_number],
                )
                return document.publication_number, output

        tasks = [asyncio.create_task(analyze(document)) for document in documents]
        try:
            results = await asyncio.gather(*tasks)
        except BaseException:
            # asyncio.gather does not cancel sibling tasks when one fails.  A workflow
            # retry must not overlap the previous attempt or write late results.
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        return dict(results)

    async def analyze(
        self,
        *,
        run_id: str,
        idea: IdeaParserOutput,
        document: FetchedDocument,
        document_id: str,
    ) -> DocumentAnalyzerOutput:
        packet = self.build_evidence_packet(run_id, document_id, idea, document)
        if not packet:
            raise AgentExecutionError(
                f"document has no usable evidence spans: {document.publication_number}"
            )
        self._persist_evidence(run_id, document_id, packet)
        evidence_aliases = {
            f"E{index}": item.evidence_id for index, item in enumerate(packet, start=1)
        }
        result = await self.agents.call_agent(
            run_id,
            "patent-document-analyzer",
            system_prompt=DOCUMENT_ANALYZER_PROMPT,
            input_payload={
                "publication_number": document.publication_number,
                "metadata": {
                    "title": document.title,
                    "assignee": document.assignee,
                    "priority_date": document.priority_date,
                    "publication_date": document.publication_date,
                },
                "features": [
                    {
                        "feature_id": feature.feature_id,
                        "feature_text": feature.feature_text,
                        "required": feature.required,
                    }
                    for feature in idea.features
                ],
                "evidence": [
                    {**item.__dict__, "evidence_id": alias}
                    for alias, item in zip(evidence_aliases, packet, strict=True)
                ],
            },
            input_size=sum(len(item.text) for item in packet),
        )
        output = result.output
        if not isinstance(output, DocumentAnalyzerOutput):
            raise AgentExecutionError("document analyzer returned wrong validated model")
        output = self._resolve_evidence_aliases(output, evidence_aliases)
        if self._identifier(output.publication_number) != self._identifier(
            document.publication_number
        ):
            # The number is immutable request metadata.  Evidence IDs below still
            # prove that the analysis used this document's packet, so correct a bad
            # model echo instead of discarding an otherwise valid analysis.
            output = output.model_copy(
                update={"publication_number": document.publication_number}
            )
        self._validate_output_identity_and_features(output, idea, document)
        allowed_evidence = {item.evidence_id for item in packet}
        cited = {
            evidence_id
            for mapping in output.feature_mappings
            for evidence_id in mapping.evidence_ids
        }
        unknown = cited - allowed_evidence
        if unknown:
            raise AgentExecutionError(f"document analyzer cited unknown evidence: {sorted(unknown)}")
        self._persist_analysis(run_id, document_id, output)
        self._release_rebuildable_text(document_id)
        return output

    def _validate_output_identity_and_features(
        self,
        output: DocumentAnalyzerOutput,
        idea: IdeaParserOutput,
        document: FetchedDocument,
    ) -> None:
        expected_features = {feature.feature_id for feature in idea.features if feature.required}
        actual_features = {mapping.feature_id for mapping in output.feature_mappings}
        if actual_features != expected_features:
            raise AgentExecutionError(
                f"document mappings do not match required features: expected={sorted(expected_features)} actual={sorted(actual_features)}"
            )

    @staticmethod
    def _resolve_evidence_aliases(
        output: DocumentAnalyzerOutput, aliases: dict[str, str]
    ) -> DocumentAnalyzerOutput:
        """Translate unambiguous model-facing E1 aliases to durable evidence IDs."""

        resolved_mappings = []
        durable_ids = set(aliases.values())
        for mapping in output.feature_mappings:
            resolved = []
            for evidence_id in mapping.evidence_ids:
                if evidence_id in durable_ids:
                    durable_id = evidence_id
                else:
                    normalized = "".join(
                        character for character in evidence_id.upper() if character.isalnum()
                    )
                    durable_id = aliases.get(normalized)
                if durable_id is None:
                    # Preserve the bad value so the existing strict validator emits
                    # a useful error instead of silently dropping a citation.
                    durable_id = evidence_id
                if durable_id not in resolved:
                    resolved.append(durable_id)
            resolved_mappings.append(mapping.model_copy(update={"evidence_ids": resolved}))
        return output.model_copy(update={"feature_mappings": resolved_mappings})

    def build_evidence_packet(
        self,
        run_id: str,
        document_id: str,
        idea: IdeaParserOutput,
        document: FetchedDocument,
    ) -> list[EvidencePacketItem]:
        terms = self._terms([feature.feature_text for feature in idea.features])
        candidates = []
        section_texts = {
            "abstract": document.abstract_text,
            "claims": document.claims_text,
            "description": document.description_text,
        }
        for section_type in ("abstract", "claims", "description"):
            section = section_texts[section_type]
            spans = document.section_spans.get(section_type, [])
            if not spans and section:
                spans = [
                    {
                        "label": section_type,
                        "start": 0,
                        "end": len(section),
                        "text": section,
                    }
                ]
            for index, span in enumerate(spans):
                text = str(span.get("text") or "").strip()
                if not text:
                    continue
                start = span.get("start")
                end = span.get("end")
                if isinstance(start, int) and isinstance(end, int) and section:
                    if start < 0 or end < start or end > len(section):
                        continue
                    if section[start:end].strip() != text:
                        continue
                else:
                    start = None
                    end = None
                score = self._score(text, terms)
                priority = 3 if section_type == "claims" else 2 if section_type == "abstract" else 1
                evidence_id = "E-" + hashlib.sha256(
                    f"{run_id}|{document_id}|{section_type}|{span.get('label')}|{text}".encode(
                        "utf-8"
                    )
                ).hexdigest()[:20]
                candidates.append(
                    (
                        priority,
                        score,
                        index,
                        EvidencePacketItem(
                            evidence_id=evidence_id,
                            section_type=section_type,
                            section_label=str(span.get("label") or f"{section_type} {index + 1}"),
                            text=text,
                            start_offset=start,
                            end_offset=end,
                            score=round(score, 4),
                        ),
                    )
                )

        always = [item for item in candidates if item[0] >= 2]
        descriptions = sorted(
            (item for item in candidates if item[0] == 1),
            key=lambda item: (-item[1], item[2]),
        )[: self.max_description_spans]
        ordered = sorted(always, key=lambda item: (-item[0], item[2])) + descriptions
        packet = []
        used = 0
        for _, _, _, item in ordered:
            if used >= self.max_packet_characters:
                break
            remaining = self.max_packet_characters - used
            if len(item.text) > remaining:
                if remaining < 200:
                    break
                item = EvidencePacketItem(
                    evidence_id=item.evidence_id,
                    section_type=item.section_type,
                    section_label=item.section_label + " (truncated)",
                    text=item.text[:remaining],
                    start_offset=item.start_offset,
                    end_offset=(item.start_offset + remaining) if item.start_offset is not None else None,
                    score=item.score,
                )
            packet.append(item)
            used += len(item.text)
        return packet

    def _persist_evidence(
        self, run_id: str, document_id: str, packet: list[EvidencePacketItem]
    ) -> None:
        with self.database.connect() as connection:
            for item in packet:
                connection.execute(
                    """
                    INSERT INTO evidence(
                        evidence_id,run_id,document_id,section_type,section_label,
                        quote_text,start_offset,end_offset,content_hash,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT (evidence_id) DO NOTHING
                    """,
                    (
                        item.evidence_id,
                        run_id,
                        document_id,
                        item.section_type,
                        item.section_label,
                        item.text,
                        item.start_offset,
                        item.end_offset,
                        hashlib.sha256(item.text.encode("utf-8")).hexdigest(),
                        now_ms(),
                    ),
                )

    def _persist_analysis(
        self, run_id: str, document_id: str, output: DocumentAnalyzerOutput
    ) -> None:
        with self.database.connect() as connection:
            existing = connection.execute(
                "SELECT COUNT(*) FROM feature_mappings WHERE run_id = ? AND document_id = ?",
                (run_id, document_id),
            ).fetchone()[0]
            if existing:
                raise AgentExecutionError("document analysis already exists for this run")
            for mapping in output.feature_mappings:
                feature_id = f"{run_id}:{mapping.feature_id}"
                connection.execute(
                    """
                    INSERT INTO feature_mappings(
                        mapping_id,run_id,document_id,feature_id,coverage_status,
                        confidence,evidence_ids_json,rationale
                    ) VALUES(?,?,?,?,?,?,?,?)
                    """,
                    (
                        str(uuid.uuid4()),
                        run_id,
                        document_id,
                        feature_id,
                        mapping.status,
                        mapping.confidence,
                        canonical_json(mapping.evidence_ids),
                        mapping.rationale,
                    ),
                )
            connection.execute(
                """
                UPDATE run_documents SET relevance = ?, screening_status = 'ANALYZED',
                    deep_reviewed = TRUE
                WHERE run_id = ? AND document_id = ?
                """,
                (output.relevance, run_id, document_id),
            )

    def _release_rebuildable_text(self, document_id: str) -> None:
        """Keep durable evidence, but release patent full text after successful analysis.

        The provider response cache is the bounded, rebuildable home for full text. The
        business database retains metadata, the full-content hash, and cited evidence.
        """
        with self.database.connect() as connection:
            pending = connection.execute(
                """SELECT 1 FROM run_documents rd
                JOIN idea_runs r ON r.run_id = rd.run_id
                WHERE rd.document_id = ? AND rd.deep_reviewed = FALSE
                  AND r.status IN ('QUEUED','RUNNING') LIMIT 1""",
                (document_id,),
            ).fetchone()
            if pending is not None:
                # patent_documents are shared by every run.  Clearing the text while
                # another run is still reviewing the same row corrupts that run.
                return
            row = connection.execute(
                "SELECT metadata_json FROM patent_documents WHERE document_id = ?",
                (document_id,),
            ).fetchone()
            if row is None:
                raise AgentExecutionError(f"unknown patent document: {document_id}")
            try:
                metadata = json.loads(row["metadata_json"] or "{}")
            except json.JSONDecodeError:
                metadata = {}
            metadata.pop("section_spans", None)
            metadata["text_retention"] = "evidence_extracted"
            connection.execute(
                """
                UPDATE patent_documents
                SET abstract_text = NULL, claims_text = NULL, description_text = NULL,
                    metadata_json = ?, updated_at = ?
                WHERE document_id = ?
                """,
                (canonical_json(metadata), now_ms(), document_id),
            )

    @staticmethod
    def _identifier(value: str) -> str:
        return "".join(character for character in value.upper() if character.isalnum())

    @staticmethod
    def _terms(values: list[str]) -> set[str]:
        terms = set()
        for value in values:
            terms.update(
                token.lower()
                for token in re.findall(r"[A-Za-z0-9_\-]{3,}|[\u4e00-\u9fff]{2,}", value)
            )
        return terms

    @staticmethod
    def _score(text: str, terms: set[str]) -> float:
        lowered = text.lower()
        return sum(1 for term in terms if term in lowered) / max(1, len(terms))
