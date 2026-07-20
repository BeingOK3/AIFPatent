from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import uuid
from pathlib import Path

from idea.database import Database
from idea.novelty import NoveltyGateError, NoveltyService


class NoveltyServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "idea.db")
        self.db.initialize()
        case = self.db.create_case("Novelty")
        self.run = self.db.create_run(
            case_id=case["case_id"], input_text="idea", evaluation_date="2026-07-16",
            date_basis="default", analysis_scope="full", model="stub", skill_version="1",
            workflow_version="1", config_snapshot={},
        )
        self.run_id = self.run["run_id"]
        with self.db.connect() as connection:
            for ordinal, external_id in enumerate(("F1", "F2"), 1):
                connection.execute(
                    "INSERT INTO idea_features VALUES(?,?,?,?,?,?,?,?)",
                    (
                        f"{self.run_id}:{external_id}", self.run_id, ordinal,
                        f"feature {external_id}", "normalized", None, None,
                        json.dumps({"external_feature_id": external_id}),
                    ),
                )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def add_document(self, number: int, statuses: tuple[str, str], *, date="2020-01-01") -> str:
        document_id = f"doc-{number}"
        publication = f"US{number}A1"
        with self.db.connect() as connection:
            connection.execute(
                """INSERT INTO patent_documents(
                    document_id,publication_number,publication_date,language,created_at,updated_at
                ) VALUES(?,?,?,?,?,?)""",
                (document_id, publication, date, "en", number, number),
            )
            connection.execute(
                """INSERT INTO run_documents(
                    run_id,document_id,screening_status,deep_reviewed
                ) VALUES(?,?,'ANALYZED',1)""",
                (self.run_id, document_id),
            )
            for ordinal, (feature_id, status) in enumerate(zip(("F1", "F2"), statuses), 1):
                evidence_ids = []
                if status in {"DISCLOSED", "PARTIAL"}:
                    evidence_id = f"E-{number}-{ordinal}"
                    quote = f"evidence {number} {ordinal}"
                    connection.execute(
                        "INSERT INTO evidence VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (
                            evidence_id, self.run_id, document_id, "claims", f"claim {ordinal}",
                            quote, 0, len(quote), hashlib.sha256(quote.encode()).hexdigest(), number,
                        ),
                    )
                    evidence_ids = [evidence_id]
                connection.execute(
                    """INSERT INTO feature_mappings(
                        mapping_id,run_id,document_id,feature_id,coverage_status,
                        confidence,evidence_ids_json,rationale
                    ) VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        str(uuid.uuid4()), self.run_id, document_id,
                        f"{self.run_id}:{feature_id}", status, 0.9,
                        json.dumps(evidence_ids), "comparison",
                    ),
                )
        return document_id

    def test_one_document_must_cover_all_features_without_cross_document_combination(self) -> None:
        self.add_document(1, ("DISCLOSED", "NOT_DISCLOSED"))
        self.add_document(2, ("NOT_DISCLOSED", "DISCLOSED"))
        result = NoveltyService(self.db, minimum_deep_reviews=2).determine(self.run_id)
        self.assertEqual(result.conclusion, "NOVEL")
        self.assertFalse(any(matrix.destroys_novelty for matrix in result.matrices))

    def test_direct_novel_result_requires_minimum_deep_reviews(self) -> None:
        with self.db.connect() as connection:
            connection.execute(
                "INSERT INTO idea_features VALUES(?,?,?,?,?,?,?,?)",
                (
                    f"{self.run_id}:F3", self.run_id, 3, "optional feature", "inferred",
                    None, None, json.dumps({"external_feature_id": "F3", "required": False}),
                ),
            )
        for number in range(1, 11):
            self.add_document(number, ("DISCLOSED", "NOT_DISCLOSED"))
        result = NoveltyService(self.db).determine(self.run_id)
        self.assertEqual(result.conclusion, "NOVEL")
        self.assertIn("具备新颖性", result.rationale)
        self.assertEqual(result.missing_features, ["F2"])
        self.assertTrue(all(len(matrix.mappings) == 2 for matrix in result.matrices))

    def test_below_minimum_is_uncertain_not_false_novel(self) -> None:
        self.add_document(1, ("DISCLOSED", "NOT_DISCLOSED"))
        result = NoveltyService(self.db).determine(self.run_id)
        self.assertEqual(result.conclusion, "UNCERTAIN")
        self.assertTrue(result.limitations)

    def test_multiple_destroying_documents_are_retained_and_one_is_selected(self) -> None:
        first = self.add_document(1, ("DISCLOSED", "DISCLOSED"))
        self.add_document(2, ("DISCLOSED", "DISCLOSED"))
        result = NoveltyService(self.db).determine(self.run_id)
        self.assertEqual(result.conclusion, "NOT_NOVEL")
        self.assertEqual(sum(matrix.destroys_novelty for matrix in result.matrices), 2)
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT * FROM novelty_results WHERE run_id = ?", (self.run_id,)
            ).fetchone()
        self.assertEqual(row["destroying_document_id"], first)

    def test_evidence_hash_mismatch_fails_closed(self) -> None:
        self.add_document(1, ("DISCLOSED", "NOT_DISCLOSED"))
        with self.db.connect() as connection:
            connection.execute("UPDATE evidence SET quote_text = 'tampered'")
        with self.assertRaisesRegex(NoveltyGateError, "hash mismatch"):
            NoveltyService(self.db, minimum_deep_reviews=1).determine(self.run_id)

    def test_post_evaluation_document_fails_closed(self) -> None:
        self.add_document(1, ("DISCLOSED", "DISCLOSED"), date="2027-01-01")
        with self.assertRaisesRegex(NoveltyGateError, "post-evaluation"):
            NoveltyService(self.db).determine(self.run_id)


if __name__ == "__main__":
    unittest.main()
