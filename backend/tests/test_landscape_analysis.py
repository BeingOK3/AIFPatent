from __future__ import annotations

import unittest

from idea.providers.base import FetchedDocument
from landscape.analysis import LandscapeAnalysisError, build_evidence_packet, validate_analysis_output
from landscape.schemas import LandscapeEvidenceRef, LandscapePatentAnalysis


class LandscapeAnalysisTests(unittest.TestCase):
    def document(self) -> FetchedDocument:
        abstract = "一种液冷系统，通过冷板降低服务器温度。"
        claims = "1. 一种液冷系统，包括冷板和循环泵。\n2. 根据权利要求1所述的系统。"
        description = "背景技术中，风冷能耗较高。液冷回路可提高散热效率。"
        return FetchedDocument(
            provider="fixture",
            publication_number="CN123A",
            application_number="CN2026001",
            title="液冷系统",
            assignee="示例公司",
            filing_date="2026-01-01",
            publication_date="2026-05-01",
            language="zh",
            url="https://example.test/CN123A",
            abstract_text=abstract,
            claims_text=claims,
            description_text=description,
            section_spans={
                "abstract": [{"label": "abstract", "start": 0, "end": len(abstract), "text": abstract}],
                "claims": [{"label": "claim 1", "start": 0, "end": len(claims), "text": claims}],
                "description": [{"label": "背景技术", "start": 0, "end": len(description), "text": description}],
            },
        )

    def packet(self):
        return build_evidence_packet(
            run_id="run-1",
            document_id="doc-1",
            document=self.document(),
            direction_terms=["液冷", "冷板"],
        )

    def valid_output(self, evidence_id: str) -> LandscapePatentAnalysis:
        return LandscapePatentAnalysis(
            publication_number="CN123A",
            prior_art="现有风冷方案能耗较高。",
            prior_art_problems=["能耗高"],
            core_invention_points=["冷板与循环泵构成液冷回路"],
            technical_problems_solved=["服务器散热"],
            beneficial_effects=["提高散热效率"],
            technical_keywords=["液冷", "冷板"],
            evidence_refs=[
                LandscapeEvidenceRef(
                    evidence_id=evidence_id,
                    supports=[
                        "prior_art", "prior_art_problem", "core_invention_point",
                        "technical_problem_solved", "beneficial_effect",
                    ],
                )
            ],
        )

    def test_packet_uses_direct_sections_with_offsets_and_hashes(self) -> None:
        packet = self.packet()
        self.assertEqual({item.section_type for item in packet}, {"ABSTRACT", "CLAIM", "BACKGROUND"})
        self.assertTrue(all(item.publication_number == "CN123A" for item in packet))
        self.assertTrue(all(len(item.content_hash) == 64 for item in packet))

    def test_unknown_evidence_and_wrong_publication_fail_closed(self) -> None:
        packet = self.packet()
        with self.assertRaisesRegex(LandscapeAnalysisError, "unknown evidence"):
            validate_analysis_output(
                self.valid_output("EV-000000000000000000000000"),
                document=self.document(),
                packet=packet,
            )
        output = self.valid_output(packet[0].evidence_id).model_copy(
            update={"publication_number": "US999A1"}
        )
        with self.assertRaisesRegex(LandscapeAnalysisError, "different publication"):
            validate_analysis_output(output, document=self.document(), packet=packet)

    def test_every_populated_analysis_section_requires_evidence_support(self) -> None:
        packet = self.packet()
        output = self.valid_output(packet[0].evidence_id).model_copy(
            update={
                "evidence_refs": [
                    LandscapeEvidenceRef(
                        evidence_id=packet[0].evidence_id,
                        supports=["prior_art", "core_invention_point"],
                    )
                ]
            }
        )
        with self.assertRaisesRegex(LandscapeAnalysisError, "lack evidence"):
            validate_analysis_output(output, document=self.document(), packet=packet)


if __name__ == "__main__":
    unittest.main()
