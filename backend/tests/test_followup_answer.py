from __future__ import annotations

import unittest

from pydantic import ValidationError

from idea.chunks import PatentChunk
from idea.context import ContextAssembler
from idea.followup import FollowupError
from idea.followup_answer import FollowupAnswer, FollowupAnswerVerifier


def patent_chunk() -> PatentChunk:
    text = "1. 一种缓存控制器，根据热度阈值选择待淘汰数据块。"
    import hashlib

    return PatentChunk(
        chunk_id="chunk-1", version_id="cv-1", publication_number="CN123456789A",
        section_type="claims", section_label="claim-1", claim_number=1,
        claim_kind="independent", parent_claim_numbers=(), start_offset=20,
        end_offset=20 + len(text), text=text,
        text_hash=hashlib.sha256(text.encode()).hexdigest(), token_count=3,
        chunker_version="v1",
    )


def context():
    return ContextAssembler().assemble(
        purpose="FOLLOWUP", run_id="run-1", turn_id="turn-1",
        corpus_snapshot_hash="a" * 64, chunks=(patent_chunk(),),
        system_prompt="仅依据证据回答。", question="F1 是否重合？",
        prompt_version="followup-v1", retriever_version="hybrid-rrf-v1",
        input_budget=200, reserved_output_tokens=40,
    )


def answer(**updates) -> FollowupAnswer:
    value = {
        "answer_type": "OVERLAP_ANALYSIS",
        "direct_answer": "F1 与 CN123456789A 的独立权利要求存在较高技术重合。",
        "citation_aliases": ["C1"],
        "overlap_items": [
            {
                "feature_id": "F1",
                "idea_feature": "按热度淘汰缓存块",
                "patent_element": "根据热度阈值选择待淘汰数据块",
                "overlap_level": "HIGH",
                "analysis": "技术手段与控制目标均相近。",
                "citation_aliases": ["C1"],
            }
        ],
        "differences": [],
        "design_around_options": [],
        "legal_boundary": "这里只评价技术重合，权利要求有效性和法律结论需要专业复核。",
        "limitations": [],
        "needs_new_research": False,
    }
    value.update(updates)
    return FollowupAnswer.model_validate(value)


class FollowupAnswerVerifierTests(unittest.TestCase):
    def verify(self, value):
        return FollowupAnswerVerifier().verify(
            value,
            context=context(),
            allowed_feature_ids=("F1", "F2"),
            allowed_publication_numbers=("CN123456789A",),
        )

    def test_valid_high_overlap_binds_only_context_aliases_to_exact_quote(self) -> None:
        verified = self.verify(answer())

        self.assertEqual(verified.used_aliases, ("C1",))
        self.assertEqual(len(verified.citations), 2)
        self.assertEqual(verified.citations[0].chunk_id, "chunk-1")
        self.assertEqual(verified.citations[0].quote_text, patent_chunk().text)
        self.assertEqual(verified.citations[0].start_offset, 20)
        self.assertEqual(verified.citations[0].end_offset, 20 + len(patent_chunk().text))

    def test_unknown_alias_and_high_overlap_without_citation_fail_closed(self) -> None:
        value = answer(citation_aliases=["C2"])
        with self.assertRaisesRegex(FollowupError, "unknown Citation alias"):
            self.verify(value)

        raw = answer().model_dump(mode="json")
        raw["overlap_items"][0]["citation_aliases"] = []
        with self.assertRaisesRegex(FollowupError, "HIGH technical overlap"):
            self.verify(FollowupAnswer.model_validate(raw))

    def test_unknown_feature_or_publication_fails_closed(self) -> None:
        raw = answer().model_dump(mode="json")
        raw["overlap_items"][0]["feature_id"] = "F9"
        with self.assertRaisesRegex(FollowupError, "unknown IDEA features"):
            self.verify(FollowupAnswer.model_validate(raw))

        value = answer(direct_answer="该方案还涉及 US9999999B2。")
        with self.assertRaisesRegex(FollowupError, "unknown publications"):
            self.verify(value)

    def test_definitive_infringement_or_non_infringement_language_is_rejected(self) -> None:
        for conclusion in ("该产品构成侵权。", "该修改保证不侵权。"):
            with self.subTest(conclusion=conclusion):
                with self.assertRaisesRegex(FollowupError, "legal conclusion boundary"):
                    self.verify(answer(direct_answer=conclusion))

    def test_legal_boundary_may_explicitly_disclaim_forbidden_conclusions(self) -> None:
        verified = self.verify(answer(
            legal_boundary="本回答只比较技术披露，不能判断构成侵权或不构成侵权。"
        ))
        self.assertIn("不能判断", verified.answer.legal_boundary)

    def test_answer_schema_rejects_bad_aliases_and_false_insufficient_claims(self) -> None:
        with self.assertRaises(ValidationError):
            answer(citation_aliases=["citation-1"])

        raw = answer().model_dump(mode="json")
        raw["answer_type"] = "INSUFFICIENT_EVIDENCE"
        with self.assertRaises(ValidationError):
            FollowupAnswer.model_validate(raw)

    def test_new_research_answer_sets_required_flag(self) -> None:
        value = answer(
            answer_type="NEW_RESEARCH_REQUIRED",
            overlap_items=[],
            citation_aliases=[],
            needs_new_research=False,
        )
        self.assertTrue(value.needs_new_research)


if __name__ == "__main__":
    unittest.main()
