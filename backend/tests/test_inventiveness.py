from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace

from idea.agent_schemas import InventiveStepOutput, NoveltyResult
from idea.agents import AgentExecutionError, IdeaAgentService
from idea.database import Database
from idea.inventiveness import InventivenessService
from idea.model_client import AgentCallResult


def feature_mapping(feature_id, status, evidence_ids=None):
    return {
        "feature_id": feature_id, "status": status, "evidence_ids": evidence_ids or [],
        "rationale": "comparison", "confidence": 0.9,
    }


class RouteModel:
    def __init__(
        self,
        *,
        unknown_evidence=False,
        force_not_inventive=False,
        omit_evidence_once=False,
    ):
        self.settings = SimpleNamespace(provider="stub")
        self.unknown_evidence = unknown_evidence
        self.force_not_inventive = force_not_inventive
        self.omit_evidence_once = omit_evidence_once
        self.calls = []
        self.active = 0
        self.max_active = 0

    async def complete(self, agent_name, **kwargs):
        payload = kwargs["input_payload"]
        self.calls.append(payload)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        analyses = []
        for feature in payload["distinguishing_features"]:
            candidates = feature["d2_candidates"]
            publications = [candidate["publication_number"] for candidate in candidates[:1]]
            evidence = [item["evidence_id"] for item in candidates[0]["evidence"]] if candidates else []
            if self.omit_evidence_once and "validation_correction" not in payload:
                evidence = []
            if self.unknown_evidence:
                evidence = ["E-invented"]
            analyses.append({
                "feature_id": feature["feature_id"],
                "d2_publication_numbers": publications,
                "evidence_ids": evidence,
                "motivation_to_combine": "YES" if self.force_not_inventive else "UNCERTAIN",
                "rationale": "candidate reviewed",
            })
        output = InventiveStepOutput.model_validate({
            "route_id": payload["route_id"],
            "d1_publication_number": payload["d1_publication_number"],
            "distinguishing_features": analyses,
            "objective_technical_problem": "improve cache behavior",
            "status": "NOT_INVENTIVE" if self.force_not_inventive else ("UNCERTAIN" if any(item["d2_candidates"] for item in payload["distinguishing_features"]) else "NEED_MORE_EVIDENCE"),
            "overall_rationale": "bounded evidence analysis",
        })
        return AgentCallResult(agent_name, "stub", output, 1, 5, {}, payload["route_id"])


class InventivenessServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "idea.db")
        self.db.initialize()
        case = self.db.create_case("Inventiveness")
        run = self.db.create_run(
            case_id=case["case_id"], input_text="idea", evaluation_date="2026-07-16",
            date_basis="default", analysis_scope="full", model="stub", skill_version="1",
            workflow_version="1", config_snapshot={},
        )
        self.run_id = run["run_id"]
        with self.db.connect() as connection:
            for ordinal, feature_id in enumerate(("F1", "F2"), 1):
                connection.execute(
                    "INSERT INTO idea_features VALUES(?,?,?,?,?,?,?,?)",
                    (
                        f"{self.run_id}:{feature_id}", self.run_id, ordinal, feature_id,
                        "normalized", None, None, json.dumps({"external_feature_id": feature_id}),
                    ),
                )
        self.add_document(1, ("DISCLOSED", "NOT_DISCLOSED"))
        self.add_document(2, ("NOT_DISCLOSED", "DISCLOSED"))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def add_document(self, number, statuses):
        document_id = f"doc-{number}"
        with self.db.connect() as connection:
            connection.execute(
                """INSERT INTO patent_documents(
                    document_id,publication_number,publication_date,language,created_at,updated_at
                ) VALUES(?,?,?,?,?,?)""",
                (document_id, f"US{number}A1", "2020-01-01", "en", number, number),
            )
            connection.execute(
                "INSERT INTO run_documents(run_id,document_id,screening_status,deep_reviewed) VALUES(?,?,'ANALYZED',1)",
                (self.run_id, document_id),
            )
            for ordinal, (feature_id, status) in enumerate(zip(("F1", "F2"), statuses), 1):
                ids = []
                if status in {"DISCLOSED", "PARTIAL"}:
                    evidence_id = f"E-{number}-{ordinal}"
                    quote = f"evidence {number}-{ordinal}"
                    connection.execute(
                        "INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (
                            evidence_id, self.run_id, document_id, "claims", f"claim {ordinal}",
                            quote, 0, len(quote), hashlib.sha256(quote.encode()).hexdigest(), number,
                        ),
                    )
                    ids = [evidence_id]
                connection.execute(
                    "INSERT INTO feature_mappings VALUES(?,?,?,?,?,?,?,?)",
                    (
                        str(uuid.uuid4()), self.run_id, document_id,
                        f"{self.run_id}:{feature_id}", status, 0.9, json.dumps(ids), "comparison",
                    ),
                )

    def novelty(self, conclusion="NOVEL"):
        matrices = [
            {
                "publication_number": "US1A1",
                "mappings": [
                    feature_mapping("F1", "DISCLOSED", ["E-1-1"]),
                    feature_mapping("F2", "NOT_DISCLOSED"),
                ],
                "destroys_novelty": False,
            },
            {
                "publication_number": "US2A1",
                "mappings": [
                    feature_mapping("F1", "NOT_DISCLOSED"),
                    feature_mapping("F2", "DISCLOSED", ["E-2-2"]),
                ],
                "destroys_novelty": False,
            },
        ]
        if conclusion == "NOT_NOVEL":
            matrices[0] = {
                "publication_number": "US1A1",
                "mappings": [
                    feature_mapping("F1", "DISCLOSED", ["E-1-1"]),
                    feature_mapping("F2", "DISCLOSED", ["E-1-2"]),
                ],
                "destroys_novelty": True,
            }
        return NoveltyResult.model_validate({
            "conclusion": conclusion,
            "confidence": 0.8,
            "matrices": matrices,
            "destroying_publication_number": "US1A1" if conclusion == "NOT_NOVEL" else None,
            "closest_publication_number": "US1A1",
            "missing_features": [] if conclusion == "NOT_NOVEL" else ["F2"],
            "rationale": "fixture novelty",
        })

    def test_multiple_d1_routes_run_concurrently_and_persist_atomically(self) -> None:
        model = RouteModel()
        service = InventivenessService(self.db, IdeaAgentService(self.db, model), max_routes=2)
        outputs = asyncio.run(service.analyze(self.run_id, self.novelty()))
        self.assertEqual([output.route_id for output in outputs], ["R1", "R2"])
        self.assertGreaterEqual(model.max_active, 2)
        with self.db.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM inventive_routes WHERE run_id = ?", (self.run_id,)
            ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all("doc-" in row["d2_document_ids_json"] for row in rows))

    def test_invented_evidence_rejects_all_route_persistence(self) -> None:
        model = RouteModel(unknown_evidence=True)
        service = InventivenessService(self.db, IdeaAgentService(self.db, model), max_routes=2)
        with self.assertRaisesRegex(AgentExecutionError, "unbound D2 evidence"):
            asyncio.run(service.analyze(self.run_id, self.novelty()))
        with self.db.connect() as connection:
            count = connection.execute("SELECT COUNT(*) FROM inventive_routes").fetchone()[0]
        self.assertEqual(count, 0)

    def test_unbound_publication_receives_one_precise_internal_correction(self) -> None:
        model = RouteModel(omit_evidence_once=True)
        service = InventivenessService(
            self.db, IdeaAgentService(self.db, model), max_routes=1
        )
        outputs = asyncio.run(service.analyze(self.run_id, self.novelty()))
        self.assertEqual(len(outputs), 1)
        self.assertEqual(len(model.calls), 2)
        correction = model.calls[1]["validation_correction"]
        self.assertIn("every cited D2 publication", correction["reason"])
        self.assertIn(
            "US2A1",
            correction["valid_evidence_by_feature_and_publication"]["F2"],
        )

    def test_not_novel_skips_inventive_model_calls(self) -> None:
        model = RouteModel()
        service = InventivenessService(self.db, IdeaAgentService(self.db, model))
        outputs = asyncio.run(service.analyze(self.run_id, self.novelty("NOT_NOVEL")))
        self.assertEqual(outputs, [])
        self.assertEqual(model.calls, [])

    def test_not_inventive_rejects_partial_only_d2_teaching(self) -> None:
        with self.db.connect() as connection:
            connection.execute(
                """UPDATE feature_mappings SET coverage_status = 'PARTIAL'
                WHERE document_id = 'doc-2' AND feature_id = ?""",
                (f"{self.run_id}:F2",),
            )
        model = RouteModel(force_not_inventive=True)
        service = InventivenessService(
            self.db, IdeaAgentService(self.db, model), max_routes=1
        )
        with self.assertRaisesRegex(AgentExecutionError, "fully disclosed D2"):
            asyncio.run(service.analyze(self.run_id, self.novelty()))


if __name__ == "__main__":
    unittest.main()
