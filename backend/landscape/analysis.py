from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass

from idea.agent_schemas import register_agent_output_model
from idea.model_client import StructuredModelClient
from idea.providers.base import FetchedDocument

from .database import LandscapeDatabase
from .schemas import EvidenceItem, LandscapePatentAnalysis


ANALYZER_NAME = "patent-landscape-analyzer"
ANALYZER_PROMPT = """
You analyze exactly one newly published patent. Use only the supplied evidence items and return
all explanations in Simplified Chinese. Explain the prior art, prior-art problems, core invention
points, technical problems solved, and beneficial effects. Every material conclusion must cite one
or more supplied evidence IDs through evidence_refs. Copy publication_number exactly. Never infer
assignee, dates, family members, legal status, or any fact absent from the packet.
"""


class LandscapeAnalysisError(RuntimeError):
    pass


@dataclass(frozen=True)
class EvidenceCandidate:
    priority: int
    score: float
    ordinal: int
    section_type: str
    section_label: str
    text: str
    start_offset: int
    end_offset: int


def build_evidence_packet(
    *,
    run_id: str,
    document_id: str,
    document: FetchedDocument,
    direction_terms: list[str],
    max_characters: int = 40_000,
    max_description_spans: int = 12,
) -> list[EvidenceItem]:
    """Build one bounded packet directly from this document; no retriever is involved."""
    terms = _terms(direction_terms)
    candidates: list[EvidenceCandidate] = []
    section_texts = {
        "abstract": document.abstract_text,
        "claims": document.claims_text,
        "description": document.description_text,
    }
    for section_name in ("abstract", "claims", "description"):
        section = section_texts[section_name]
        spans = document.section_spans.get(section_name, [])
        if not spans and section:
            spans = [{"label": section_name, "start": 0, "end": len(section), "text": section}]
        for ordinal, raw_span in enumerate(spans):
            text = str(raw_span.get("text") or "").strip()
            start = raw_span.get("start")
            end = raw_span.get("end")
            if not text or not isinstance(start, int) or not isinstance(end, int):
                continue
            if start < 0 or end <= start or end > len(section):
                continue
            if section[start:end].strip() != text:
                continue
            label = str(raw_span.get("label") or f"{section_name} {ordinal + 1}")
            section_type, priority = _classify_section(section_name, label, text)
            candidates.append(
                EvidenceCandidate(
                    priority=priority,
                    score=_term_score(text, terms),
                    ordinal=ordinal,
                    section_type=section_type,
                    section_label=label,
                    text=text,
                    start_offset=start,
                    end_offset=end,
                )
            )

    core = [item for item in candidates if item.priority >= 3]
    descriptions = sorted(
        (item for item in candidates if item.priority < 3),
        key=lambda item: (-item.priority, -item.score, item.ordinal),
    )[:max_description_spans]
    ordered = sorted(core, key=lambda item: (-item.priority, item.ordinal)) + descriptions
    packet: list[EvidenceItem] = []
    used = 0
    for candidate in ordered:
        remaining = max_characters - used
        if remaining <= 0:
            break
        text = candidate.text
        end_offset = candidate.end_offset
        label = candidate.section_label
        if len(text) > remaining:
            if remaining < 200:
                break
            text = text[:remaining]
            end_offset = candidate.start_offset + len(text)
            label += " (truncated)"
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        evidence_id = "EV-" + hashlib.sha256(
            f"{run_id}|{document_id}|{candidate.section_type}|{candidate.start_offset}|{digest}".encode()
        ).hexdigest()[:24]
        packet.append(
            EvidenceItem(
                evidence_id=evidence_id,
                publication_number=document.publication_number,
                section_type=candidate.section_type,
                section_label=label,
                text=text,
                start_offset=candidate.start_offset,
                end_offset=end_offset,
                content_hash=digest,
            )
        )
        used += len(text)
    return packet


def validate_analysis_output(
    output: LandscapePatentAnalysis,
    *,
    document: FetchedDocument,
    packet: list[EvidenceItem],
) -> None:
    if _identifier(output.publication_number) != _identifier(document.publication_number):
        raise LandscapeAnalysisError("analyzer returned a different publication number")
    allowed = {item.evidence_id for item in packet if item.publication_number == document.publication_number}
    cited = {reference.evidence_id for reference in output.evidence_refs}
    unknown = cited - allowed
    if unknown:
        raise LandscapeAnalysisError(f"analyzer cited unknown evidence: {sorted(unknown)}")
    required = {"prior_art", "core_invention_point"}
    if output.prior_art_problems:
        required.add("prior_art_problem")
    if output.technical_problems_solved:
        required.add("technical_problem_solved")
    if output.beneficial_effects:
        required.add("beneficial_effect")
    supported = {support for reference in output.evidence_refs for support in reference.supports}
    missing = required - supported
    if missing:
        raise LandscapeAnalysisError(f"analysis sections lack evidence support: {sorted(missing)}")


class LandscapeAnalysisService:
    def __init__(
        self,
        model: StructuredModelClient,
        database: LandscapeDatabase | None = None,
        *,
        concurrency: int = 3,
        max_packet_characters: int = 40_000,
    ):
        register_agent_output_model(ANALYZER_NAME, LandscapePatentAnalysis)
        self.model = model
        self.database = database
        self.concurrency = concurrency
        self.max_packet_characters = max_packet_characters

    async def analyze(
        self,
        *,
        run_id: str,
        document_id: str,
        document: FetchedDocument,
        direction_terms: list[str],
    ) -> tuple[LandscapePatentAnalysis, list[EvidenceItem]]:
        packet = build_evidence_packet(
            run_id=run_id,
            document_id=document_id,
            document=document,
            direction_terms=direction_terms,
            max_characters=self.max_packet_characters,
        )
        if not packet:
            raise LandscapeAnalysisError(f"document has no usable evidence: {document.publication_number}")
        result = await self.model.complete(
            ANALYZER_NAME,
            system_prompt=ANALYZER_PROMPT,
            input_payload={
                "publication_number": document.publication_number,
                "metadata": {
                    "title": document.title,
                    "application_number": document.application_number,
                    "filing_date": document.filing_date,
                    "publication_date": document.publication_date,
                    "assignee": document.assignee,
                },
                "evidence": [item.model_dump(mode="json") for item in packet],
            },
        )
        output = result.output
        if not isinstance(output, LandscapePatentAnalysis):
            raise LandscapeAnalysisError("landscape analyzer returned the wrong schema")
        validate_analysis_output(output, document=document, packet=packet)
        if self.database is not None:
            self.database.put_evidence(
                run_id, document_id, [item.model_dump(mode="json") for item in packet]
            )
            self.database.put_analysis(
                run_id,
                document_id,
                document.publication_number,
                output.model_dump(mode="json"),
            )
        return output, packet

    async def analyze_many(
        self,
        *,
        run_id: str,
        documents: list[tuple[str, FetchedDocument]],
        direction_terms: list[str],
        batch_size: int | None = None,
    ) -> tuple[dict[str, LandscapePatentAnalysis], dict[str, str]]:
        semaphore = asyncio.Semaphore(self.concurrency)

        async def one(document_id: str, document: FetchedDocument):
            async with semaphore:
                try:
                    output, _ = await self.analyze(
                        run_id=run_id,
                        document_id=document_id,
                        document=document,
                        direction_terms=direction_terms,
                    )
                    return document.publication_number, output, None
                except Exception as exc:
                    return document.publication_number, None, f"{type(exc).__name__}: {str(exc)[:500]}"

        size = max(1, batch_size or len(documents) or 1)
        results = []
        for offset in range(0, len(documents), size):
            # Batching bounds task creation without changing the eligible set.
            results.extend(
                await asyncio.gather(
                    *(
                        one(document_id, document)
                        for document_id, document in documents[offset : offset + size]
                    )
                )
            )
        analyses = {publication: output for publication, output, _ in results if output is not None}
        failures = {publication: error for publication, _, error in results if error is not None}
        return analyses, failures


def _classify_section(section_name: str, label: str, text: str) -> tuple[str, int]:
    if section_name == "abstract":
        return "ABSTRACT", 4
    if section_name == "claims":
        return "CLAIM", 5
    sample = f"{label} {text[:120]}".casefold()
    if "background" in sample or "背景" in sample or "现有技术" in sample:
        return "BACKGROUND", 3
    return "DESCRIPTION", 2


def _terms(values: list[str]) -> set[str]:
    result: set[str] = set()
    for value in values:
        result.update(
            token.casefold()
            for token in re.findall(r"[A-Za-z0-9_-]{3,}|[\u3400-\u9fff]{2,}", value)
        )
    return result


def _term_score(text: str, terms: set[str]) -> float:
    lowered = text.casefold()
    return sum(term in lowered for term in terms) / max(1, len(terms))


def _identifier(value: str) -> str:
    return "".join(character for character in value.upper() if character.isalnum())
