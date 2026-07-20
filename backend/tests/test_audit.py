from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace

from idea.agent_schemas import EvidenceAuditorOutput, NoveltyResult, ValueAnalyzerOutput
from idea.agents import AgentExecutionError, IdeaAgentService
from idea.audit import AuditService
from idea.database import Database, canonical_json
from idea.model_client import AgentCallResult


class AuditModel:
    def __init__(
        self, *, omit_evidence=False, omit_publication_once=False, critical=False
    ):
        self.settings = SimpleNamespace(provider="stub")
        self.omit_evidence = omit_evidence
        self.omit_publication_once = omit_publication_once
        self.critical = critical
        self.calls = 0

    async def complete(self, agent_name, **kwargs):
        self.calls += 1
        payload = kwargs["input_payload"]
        evidence = [item["evidence_id"] for item in payload["evidence_inventory"]]
        if self.omit_evidence:
            evidence = evidence[:-1]
        publications = payload["publication_numbers"]
        if self.omit_publication_once and self.calls == 1:
            publications = publications[:-1]
        issues = []
        if self.critical:
            issues.append({
                "severity": "critical", "code": "SEMANTIC_OVERSTATEMENT",
                "message": "language may be broader than quote",
                "evidence_ids": evidence[:1],
            })
        output = EvidenceAuditorOutput.model_validate({
            "issues": issues,
            "checked_evidence_ids": evidence,
            "checked_publication_numbers": publications,
        })
        return AgentCallResult(agent_name, "stub", output, 1, 5, {}, "audit-1")


def value():
    return ValueAnalyzerOutput.model_validate({
        "detectability": {"rating": 3, "rationale": "依据", "evidence_basis": ["IDEA:F1"]},
        "workaround_difficulty": {"rating": 3, "rationale": "依据", "evidence_basis": ["IDEA:F1"]},
        "technical_market_value": {"rating": 3, "rationale": "依据", "evidence_basis": ["IDEA:F1"]},
        "alternative_paths": ["path one", "path two"], "recommendation": "WATCH",
        "rationale": "preliminary",
    })


class AuditServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "idea.db")
        self.db.initialize()
        case = self.db.create_case("Audit")
        run = self.db.create_run(
            case_id=case["case_id"], input_text="idea", evaluation_date="2026-07-16",
            date_basis="default", analysis_scope="full", model="stub", skill_version="1",
            workflow_version="1", config_snapshot={},
        )
        self.run_id = run["run_id"]
        self.document_id = "doc-1"
        with self.db.connect() as connection:
            connection.execute(
                "INSERT INTO idea_features VALUES(?,?,?,?,?,?,?,?)",
                (f"{self.run_id}:F1", self.run_id, 1, "feature", "normalized", None, None, json.dumps({"external_feature_id": "F1"})),
            )
            connection.execute(
                """INSERT INTO patent_documents(
                    document_id,publication_number,publication_date,language,created_at,updated_at
                ) VALUES(?,?,?,?,?,?)""",
                (self.document_id, "US1A1", "2020-01-01", "en", 1, 1),
            )
            connection.execute(
                "INSERT INTO run_documents(run_id,document_id,screening_status,deep_reviewed) VALUES(?,?,'ANALYZED',1)",
                (self.run_id, self.document_id),
            )
            quote = "claim evidence"
            connection.execute(
                "INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("E1", self.run_id, self.document_id, "claims", "claim 1", quote, 0, len(quote), hashlib.sha256(quote.encode()).hexdigest(), 1),
            )
            connection.execute(
                "INSERT INTO feature_mappings VALUES(?,?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), self.run_id, self.document_id, f"{self.run_id}:F1", "DISCLOSED", 0.9, json.dumps(["E1"]), "claim"),
            )
        self.novelty = NoveltyResult.model_validate({
            "conclusion": "NOT_NOVEL", "confidence": 0.9,
            "matrices": [{
                "publication_number": "US1A1",
                "mappings": [{"feature_id": "F1", "status": "DISCLOSED", "evidence_ids": ["E1"], "rationale": "claim", "confidence": 0.9}],
                "destroys_novelty": True,
            }],
            "destroying_publication_number": "US1A1", "closest_publication_number": "US1A1",
            "missing_features": [], "rationale": "single document",
        })
        with self.db.connect() as connection:
            connection.execute(
                "INSERT INTO novelty_results VALUES(?,?,?,?,?,?,?)",
                (self.run_id, "NOT_NOVEL", 0.9, self.document_id, canonical_json(self.novelty.model_dump(mode="json")), self.novelty.rationale, 1),
            )
            connection.execute(
                "INSERT INTO value_results VALUES(?,?,?)",
                (self.run_id, canonical_json(value().model_dump(mode="json")), 1),
            )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_auditor_must_prove_it_checked_exact_inventory(self) -> None:
        model = AuditModel(omit_evidence=True)
        service = AuditService(self.db, IdeaAgentService(self.db, model), minimum_deep_reviews=1)
        with self.assertRaisesRegex(AgentExecutionError, "exact evidence inventory"):
            asyncio.run(service.audit(self.run_id, self.novelty, [], value()))
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM audit_results").fetchone()[0], 0)

    def test_incomplete_publication_inventory_gets_one_corrective_retry(self) -> None:
        model = AuditModel(omit_publication_once=True)
        service = AuditService(self.db, IdeaAgentService(self.db, model), minimum_deep_reviews=1)
        findings = asyncio.run(service.audit(self.run_id, self.novelty, [], value()))
        self.assertEqual(model.calls, 2)
        self.assertEqual(findings[0]["code"], "AUDIT_COMPLETED")

    def test_clean_deterministic_and_semantic_audit_persists_completion(self) -> None:
        with self.db.connect() as connection:
            connection.execute(
                "INSERT INTO idea_features VALUES(?,?,?,?,?,?,?,?)",
                (
                    f"{self.run_id}:F2", self.run_id, 2, "optional", "inferred", None,
                    None, json.dumps({"external_feature_id": "F2", "required": False}),
                ),
            )
        model = AuditModel()
        service = AuditService(self.db, IdeaAgentService(self.db, model), minimum_deep_reviews=1)
        findings = asyncio.run(service.audit(self.run_id, self.novelty, [], value()))
        self.assertEqual(findings[0]["code"], "AUDIT_COMPLETED")
        with self.db.connect() as connection:
            row = connection.execute("SELECT * FROM audit_results").fetchone()
        self.assertEqual(row["severity"], "info")

    def test_hash_failure_is_programmatic_critical_and_skips_model(self) -> None:
        with self.db.connect() as connection:
            connection.execute("UPDATE evidence SET quote_text = 'tampered'")
        model = AuditModel()
        service = AuditService(self.db, IdeaAgentService(self.db, model), minimum_deep_reviews=1)
        findings = asyncio.run(service.audit(self.run_id, self.novelty, [], value()))
        self.assertIn("EVIDENCE_HASH_MISMATCH", {item["code"] for item in findings})
        self.assertEqual(model.calls, 0)
        self.assertTrue(any(item["severity"] == "critical" for item in findings))

    def test_model_critical_is_advisory_warning(self) -> None:
        model = AuditModel(critical=True)
        service = AuditService(self.db, IdeaAgentService(self.db, model), minimum_deep_reviews=1)
        findings = asyncio.run(service.audit(self.run_id, self.novelty, [], value()))
        model_issue = next(item for item in findings if item["code"].startswith("MODEL_"))
        self.assertEqual(model_issue["severity"], "warning")
        self.assertEqual(model_issue["details"]["reported_severity"], "critical")


if __name__ == "__main__":
    unittest.main()
