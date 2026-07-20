from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from idea.agent_schemas import DocumentAnalyzerOutput, IdeaParserOutput
from idea.agents import AgentExecutionError, IdeaAgentService
from idea.database import Database
from idea.document_analysis import DocumentAnalysisService
from idea.model_client import AgentCallResult
from idea.providers import FetchedDocument


class StubModel:
    def __init__(self, output):
        self.output = output
        self.settings = SimpleNamespace(provider="stub")
        self.payload = None

    async def complete(self, agent_name, **kwargs):
        self.payload = kwargs["input_payload"]
        return AgentCallResult(agent_name, "stub", self.output, 1, 5, {}, "r1")


def idea():
    return IdeaParserOutput.model_validate({
        "title": "缓存淘汰",
        "technical_domains": ["存储"],
        "application_scenario": "GPU",
        "technical_problem": "减少未命中",
        "features": [
            {"feature_id": "F1", "feature_text": "计算token热度", "source_type": "normalized", "required": True},
            {"feature_id": "F2", "feature_text": "按照热度淘汰缓存", "source_type": "normalized", "required": True},
        ],
        "claimed_effects": ["减少重计算"],
        "subject_types": ["method"],
        "scope_breadth": "narrow",
    })


def document():
    abstract = "A cache controller computes token heat."
    claims = "1. computing token heat for cached data\n\n2. evicting cached data according to token heat"
    description = "[0001] unrelated background\n\n[0002] token heat controls cache eviction"
    return FetchedDocument(
        provider="fixture", publication_number="US1A1", title="Token heat eviction",
        url="https://example.test/US1A1", abstract_text=abstract, claims_text=claims,
        description_text=description,
        section_spans={
            "abstract": [{"label": "abstract", "start": 0, "end": len(abstract), "text": abstract}],
            "claims": [
                {"label": "claim 1", "start": 0, "end": 39, "text": claims[:39]},
                {"label": "claim 2", "start": 41, "end": len(claims), "text": claims[41:]},
            ],
            "description": [
                {"label": "[0001]", "start": 0, "end": 27, "text": description[:27]},
                {"label": "[0002]", "start": 29, "end": len(description), "text": description[29:]},
            ],
        },
    )


class DocumentAnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "idea.db")
        self.db.initialize()
        case = self.db.create_case("Doc analysis")
        self.run = self.db.create_run(
            case_id=case["case_id"], input_text="idea", evaluation_date="2026-07-16",
            date_basis="default", analysis_scope="full", model="stub", skill_version="1",
            workflow_version="1", config_snapshot={},
        )
        self.document_id = "doc-1"
        fetched = document()
        with self.db.connect() as connection:
            connection.execute(
                """INSERT INTO patent_documents(
                    document_id,publication_number,language,abstract_text,claims_text,
                    description_text,content_hash,metadata_json,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    self.document_id, "US1A1", "en", fetched.abstract_text,
                    fetched.claims_text, fetched.description_text, "full-content-hash",
                    json.dumps({"section_spans": fetched.section_spans, "source": "fixture"}),
                    1, 1,
                ),
            )
            connection.execute(
                "INSERT INTO run_documents(run_id,document_id) VALUES(?,?)",
                (self.run["run_id"], self.document_id),
            )
            for index, feature in enumerate(idea().features, 1):
                connection.execute(
                    "INSERT INTO idea_features VALUES(?,?,?,?,?,?,?,?)",
                    (f"{self.run['run_id']}:{feature.feature_id}", self.run["run_id"], index,
                     feature.feature_text, feature.source_type, None, None,
                     '{"external_feature_id":"' + feature.feature_id + '"}'),
                )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def output(self, evidence_ids, unknown=False):
        return DocumentAnalyzerOutput.model_validate({
            "publication_number": "US1A1",
            "technical_problem": "reduce cache misses",
            "technical_solution": "compute token heat and evict cold cache",
            "technical_effect": "less recomputation",
            "application_scenario": "cache controller",
            "independent_claim_summary": "heat-based cache eviction",
            "relevance": "HIGH",
            "relevance_rationale": "same means",
            "feature_mappings": [
                {"feature_id": "F1", "status": "DISCLOSED", "evidence_ids": ["E-unknown" if unknown else evidence_ids[0]], "rationale": "claim", "confidence": 0.9},
                {"feature_id": "F2", "status": "DISCLOSED", "evidence_ids": [evidence_ids[1]], "rationale": "claim", "confidence": 0.9},
            ],
        })

    def test_packet_uses_real_spans_and_valid_analysis_persists(self) -> None:
        placeholder_model = StubModel(None)
        service = DocumentAnalysisService(self.db, IdeaAgentService(self.db, placeholder_model))
        packet = service.build_evidence_packet(self.run["run_id"], self.document_id, idea(), document())
        self.assertGreaterEqual(len(packet), 4)
        placeholder_model.output = self.output([packet[1].evidence_id, packet[2].evidence_id])
        result = asyncio.run(service.analyze(
            run_id=self.run["run_id"], idea=idea(), document=document(), document_id=self.document_id
        ))
        self.assertEqual(result.relevance, "HIGH")
        with self.db.connect() as connection:
            evidence_count = connection.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]
            mapping_count = connection.execute("SELECT COUNT(*) FROM feature_mappings").fetchone()[0]
            reviewed = connection.execute("SELECT deep_reviewed FROM run_documents").fetchone()[0]
            retained = connection.execute(
                """SELECT abstract_text,claims_text,description_text,content_hash,metadata_json
                FROM patent_documents WHERE document_id = ?""", (self.document_id,)
            ).fetchone()
        self.assertEqual(evidence_count, len(packet))
        self.assertEqual(mapping_count, 2)
        self.assertEqual(reviewed, 1)
        self.assertIsNone(retained["abstract_text"])
        self.assertIsNone(retained["claims_text"])
        self.assertIsNone(retained["description_text"])
        self.assertEqual(retained["content_hash"], "full-content-hash")
        retained_metadata = json.loads(retained["metadata_json"])
        self.assertNotIn("section_spans", retained_metadata)
        self.assertEqual(retained_metadata["text_retention"], "evidence_extracted")
        sent = placeholder_model.payload["evidence"]
        self.assertLessEqual(sum(len(item["text"]) for item in sent), 40_000)

    def test_unknown_evidence_id_is_rejected(self) -> None:
        probe = DocumentAnalysisService(self.db, IdeaAgentService(self.db, StubModel(None)))
        packet = probe.build_evidence_packet(self.run["run_id"], self.document_id, idea(), document())
        model = StubModel(self.output([packet[0].evidence_id, packet[1].evidence_id], unknown=True))
        service = DocumentAnalysisService(self.db, IdeaAgentService(self.db, model))
        with self.assertRaisesRegex(AgentExecutionError, "unknown evidence"):
            asyncio.run(service.analyze(
                run_id=self.run["run_id"], idea=idea(), document=document(), document_id=self.document_id
            ))
        with self.db.connect() as connection:
            mappings = connection.execute("SELECT COUNT(*) FROM feature_mappings").fetchone()[0]
        self.assertEqual(mappings, 0)

    def test_model_facing_evidence_aliases_are_resolved_to_durable_ids(self) -> None:
        probe = DocumentAnalysisService(self.db, IdeaAgentService(self.db, StubModel(None)))
        packet = probe.build_evidence_packet(self.run["run_id"], self.document_id, idea(), document())
        model = StubModel(self.output(["E-1", "e_2"]))
        service = DocumentAnalysisService(self.db, IdeaAgentService(self.db, model))

        result = asyncio.run(service.analyze(
            run_id=self.run["run_id"], idea=idea(), document=document(), document_id=self.document_id
        ))

        self.assertEqual(result.feature_mappings[0].evidence_ids, [packet[0].evidence_id])
        self.assertEqual(result.feature_mappings[1].evidence_ids, [packet[1].evidence_id])
        self.assertEqual(model.payload["evidence"][0]["evidence_id"], "E1")
        with self.db.connect() as connection:
            stored = connection.execute(
                "SELECT evidence_ids_json FROM feature_mappings ORDER BY rowid"
            ).fetchall()
        self.assertEqual(json.loads(stored[0]["evidence_ids_json"]), [packet[0].evidence_id])
        self.assertEqual(json.loads(stored[1]["evidence_ids_json"]), [packet[1].evidence_id])

    def test_missing_required_feature_mapping_is_rejected(self) -> None:
        probe = DocumentAnalysisService(self.db, IdeaAgentService(self.db, StubModel(None)))
        packet = probe.build_evidence_packet(self.run["run_id"], self.document_id, idea(), document())
        incomplete = self.output([packet[0].evidence_id, packet[1].evidence_id]).model_copy(
            update={"feature_mappings": [self.output([packet[0].evidence_id, packet[1].evidence_id]).feature_mappings[0]]}
        )
        service = DocumentAnalysisService(self.db, IdeaAgentService(self.db, StubModel(incomplete)))
        with self.assertRaisesRegex(AgentExecutionError, "required features"):
            asyncio.run(service.analyze(
                run_id=self.run["run_id"], idea=idea(), document=document(), document_id=self.document_id
            ))

    def test_publication_number_echo_is_canonicalized(self) -> None:
        probe = DocumentAnalysisService(self.db, IdeaAgentService(self.db, StubModel(None)))
        packet = probe.build_evidence_packet(self.run["run_id"], self.document_id, idea(), document())
        wrong_echo = self.output([packet[0].evidence_id, packet[1].evidence_id]).model_copy(
            update={"publication_number": "invented-number"}
        )
        service = DocumentAnalysisService(self.db, IdeaAgentService(self.db, StubModel(wrong_echo)))

        result = asyncio.run(service.analyze(
            run_id=self.run["run_id"], idea=idea(), document=document(), document_id=self.document_id
        ))

        self.assertEqual(result.publication_number, "US1A1")

    def test_shared_document_text_is_retained_while_another_run_is_pending(self) -> None:
        second = self.db.create_run(
            case_id=self.run["case_id"], input_text="second idea",
            evaluation_date="2026-07-16", date_basis="default", analysis_scope="full",
            model="stub", skill_version="1", workflow_version="1", config_snapshot={},
        )
        with self.db.connect() as connection:
            connection.execute(
                "INSERT INTO run_documents(run_id,document_id) VALUES(?,?)",
                (second["run_id"], self.document_id),
            )
        probe = DocumentAnalysisService(self.db, IdeaAgentService(self.db, StubModel(None)))
        packet = probe.build_evidence_packet(self.run["run_id"], self.document_id, idea(), document())
        service = DocumentAnalysisService(
            self.db,
            IdeaAgentService(self.db, StubModel(self.output([packet[0].evidence_id, packet[1].evidence_id]))),
        )

        asyncio.run(service.analyze(
            run_id=self.run["run_id"], idea=idea(), document=document(), document_id=self.document_id
        ))

        with self.db.connect() as connection:
            retained = connection.execute(
                "SELECT abstract_text,claims_text,description_text FROM patent_documents WHERE document_id = ?",
                (self.document_id,),
            ).fetchone()
        self.assertTrue(retained["abstract_text"])
        self.assertTrue(retained["claims_text"])
        self.assertTrue(retained["description_text"])

        self.db.set_run_status(second["run_id"], "CANCELLED")
        service._release_rebuildable_text(self.document_id)
        with self.db.connect() as connection:
            released = connection.execute(
                "SELECT abstract_text,claims_text,description_text FROM patent_documents WHERE document_id = ?",
                (self.document_id,),
            ).fetchone()
        self.assertIsNone(released["abstract_text"])
        self.assertIsNone(released["claims_text"])
        self.assertIsNone(released["description_text"])

    def test_analyze_many_cancels_siblings_after_failure(self) -> None:
        cancelled = asyncio.Event()

        class FailingService(DocumentAnalysisService):
            async def analyze(self, *, document, **kwargs):
                if document.publication_number == "FAIL":
                    raise AgentExecutionError("first document failed")
                try:
                    await asyncio.sleep(60)
                except asyncio.CancelledError:
                    cancelled.set()
                    raise

        failing = document().model_copy(update={"publication_number": "FAIL"})
        waiting = document().model_copy(update={"publication_number": "WAIT"})
        service = FailingService(self.db, IdeaAgentService(self.db, StubModel(None)), concurrency=2)

        with self.assertRaisesRegex(AgentExecutionError, "first document failed"):
            asyncio.run(service.analyze_many(
                run_id=self.run["run_id"], idea=idea(), documents=[failing, waiting],
                document_ids={"FAIL": "doc-fail", "WAIT": "doc-wait"},
            ))
        self.assertTrue(cancelled.is_set())


if __name__ == "__main__":
    unittest.main()
