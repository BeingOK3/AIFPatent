from __future__ import annotations

import asyncio
import hashlib
import unittest
from types import SimpleNamespace

from idea.agent_schemas import IdeaParserOutput
from idea.chunks import PatentChunk
from idea.context import ContextAssembler, ContextAssemblyError
from idea.lexical import LexicalHit
from idea.report_rag import InitialReportRagService
from idea.report_retrieval import ReportEvidenceHit, ReportRetrievalSelection


def idea() -> IdeaParserOutput:
    return IdeaParserOutput.model_validate({
        "title": "cache", "technical_domains": ["storage"],
        "application_scenario": "GPU", "technical_problem": "reduce misses",
        "features": [{
            "feature_id": "F1", "feature_text": "heat eviction",
            "source_type": "normalized", "required": True,
        }],
        "claimed_effects": ["less work"], "subject_types": ["method"],
        "scope_breadth": "narrow",
    })


def chunk(chunk_id: str, section: str, label: str) -> PatentChunk:
    text = f"{label} evidence"
    return PatentChunk(
        chunk_id=chunk_id, version_id="cv-1", publication_number="US1A1",
        section_type=section, section_label=label,
        claim_number=1 if section == "claims" else None,
        claim_kind="independent" if section == "claims" else None,
        parent_claim_numbers=(), start_offset=0, end_offset=len(text), text=text,
        text_hash=hashlib.sha256(text.encode()).hexdigest(), token_count=2,
        chunker_version="v1",
    )


class InitialReportRagServiceTests(unittest.TestCase):
    def test_budget_cannot_exclude_mandatory_evidence(self) -> None:
        abstract = chunk("chunk-a", "abstract", "abstract")
        claim = chunk("chunk-c", "claims", "claim-1")
        selections = (
            ReportRetrievalSelection(
                "F1", "doc-1", "cv-1", "US1A1",
                ReportEvidenceHit.from_lexical(
                    LexicalHit("RQ-1", 1, 0.0, "forced_abstract", abstract)
                ),
                "forced_abstract",
            ),
            ReportRetrievalSelection(
                "F1", "doc-1", "cv-1", "US1A1",
                ReportEvidenceHit.from_lexical(
                    LexicalHit("RQ-1", 1, 0.0, "forced_claim", claim)
                ),
                "forced_claim",
            ),
        )

        class Retriever:
            async def retrieve(self, **kwargs):
                return SimpleNamespace(
                    selections=selections,
                    queries=(SimpleNamespace(document_id="doc-1"),),
                    retriever_version="rv1", limitations=(),
                )

        class Contexts:
            async def put_if_absent(self, *args, **kwargs):
                raise AssertionError("incomplete Context must not be persisted")

        service = InitialReportRagService(
            Retriever(), Contexts(),
            assembler=ContextAssembler(token_counter=lambda _text: 1),
            input_budget=3, reserved_output_tokens=1,
        )
        with self.assertRaisesRegex(ContextAssemblyError, "mandatory"):
            asyncio.run(service.prepare(
                run_id="run-1", idea=idea(), corpus_snapshot_hash="a" * 64,
                allowed_version_ids=("cv-1",),
            ))


if __name__ == "__main__":
    unittest.main()
