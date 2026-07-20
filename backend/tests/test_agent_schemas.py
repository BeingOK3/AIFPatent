from __future__ import annotations

import unittest

from pydantic import ValidationError

from idea.agent_schemas import (
    DocumentAnalyzerOutput,
    FeatureMapping,
    InventiveStepOutput,
    NoveltyResult,
    agent_json_schema,
    validate_agent_output,
)


def mapping(feature_id, status, evidence=None):
    return {
        "feature_id": feature_id,
        "status": status,
        "evidence_ids": evidence or [],
        "rationale": "evidence comparison",
        "confidence": 0.9,
    }


class AgentSchemaTests(unittest.TestCase):
    def test_disclosed_feature_requires_real_evidence_id(self) -> None:
        with self.assertRaises(ValidationError):
            FeatureMapping.model_validate(mapping("F1", "DISCLOSED"))
        valid = FeatureMapping.model_validate(mapping("F1", "DISCLOSED", ["E1"]))
        self.assertEqual(valid.evidence_ids, ["E1"])

    def test_document_feature_ids_must_be_unique(self) -> None:
        base = {
            "publication_number": "US1A1",
            "technical_problem": "problem",
            "technical_solution": "solution",
            "technical_effect": "effect",
            "application_scenario": "scenario",
            "independent_claim_summary": "claim summary",
            "relevance": "HIGH",
            "relevance_rationale": "same problem and means",
            "feature_mappings": [mapping("F1", "NOT_DISCLOSED"), mapping("F1", "UNCERTAIN")],
        }
        with self.assertRaises(ValidationError):
            DocumentAnalyzerOutput.model_validate(base)

    def test_not_novel_requires_one_document_covering_every_feature(self) -> None:
        invalid = {
            "conclusion": "NOT_NOVEL",
            "confidence": 0.9,
            "matrices": [{
                "publication_number": "US1A1",
                "mappings": [mapping("F1", "DISCLOSED", ["E1"]), mapping("F2", "NOT_DISCLOSED")],
                "destroys_novelty": True,
            }],
            "destroying_publication_number": "US1A1",
            "closest_publication_number": "US1A1",
            "missing_features": [],
            "rationale": "single document",
        }
        with self.assertRaises(ValidationError):
            NoveltyResult.model_validate(invalid)

    def test_direct_novel_conclusion_is_allowed_with_missing_features(self) -> None:
        valid = NoveltyResult.model_validate({
            "conclusion": "NOVEL",
            "confidence": 0.78,
            "matrices": [{
                "publication_number": "US1A1",
                "mappings": [mapping("F1", "DISCLOSED", ["E1"]), mapping("F2", "NOT_DISCLOSED")],
                "destroys_novelty": False,
            }],
            "destroying_publication_number": None,
            "closest_publication_number": "US1A1",
            "missing_features": ["F2"],
            "rationale": "No single document discloses every required feature.",
            "limitations": ["one provider unavailable"],
        })
        self.assertEqual(valid.conclusion, "NOVEL")

    def test_multiple_independently_destroying_documents_are_allowed(self) -> None:
        matrices = [
            {
                "publication_number": publication,
                "mappings": [mapping("F1", "DISCLOSED", [f"E-{publication}"])],
                "destroys_novelty": True,
            }
            for publication in ("US1A1", "US2A1")
        ]
        valid = NoveltyResult.model_validate({
            "conclusion": "NOT_NOVEL",
            "confidence": 0.91,
            "matrices": matrices,
            "destroying_publication_number": "US2A1",
            "closest_publication_number": "US1A1",
            "missing_features": [],
            "rationale": "either document independently destroys novelty",
        })
        self.assertEqual(valid.destroying_publication_number, "US2A1")

    def test_not_inventive_requires_d2_evidence_for_every_difference(self) -> None:
        value = {
            "route_id": "R1",
            "d1_publication_number": "US1A1",
            "distinguishing_features": [{
                "feature_id": "F2",
                "d2_publication_numbers": [],
                "evidence_ids": [],
                "motivation_to_combine": "YES",
                "rationale": "asserted but unsupported",
            }],
            "objective_technical_problem": "improve cache hit rate",
            "status": "NOT_INVENTIVE",
            "overall_rationale": "combination",
        }
        with self.assertRaises(ValidationError):
            InventiveStepOutput.model_validate(value)
        value["status"] = "NEED_MORE_EVIDENCE"
        self.assertEqual(
            InventiveStepOutput.model_validate(value).status, "NEED_MORE_EVIDENCE"
        )

    def test_agent_registry_returns_json_schema_and_rejects_unknown_agent(self) -> None:
        schema = agent_json_schema("patent-document-analyzer")
        self.assertEqual(schema["title"], "DocumentAnalyzerOutput")
        with self.assertRaises(ValueError):
            validate_agent_output("imaginary-agent", {})


if __name__ == "__main__":
    unittest.main()
