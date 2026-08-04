from __future__ import annotations

import unittest
from contextlib import contextmanager
from datetime import date

from landscape.scale_gate import ScaleEstimate, ScaleTier
from landscape.scale_repository import PostgreSQLScaleGateRepository, ScaleDecision, ScaleGatePersistenceError


class Cursor:
    def __init__(self, row=None): self.row = row
    def fetchone(self): return self.row


class Connection:
    def __init__(self):
        self.run = {"run_id":"LRN-0000000000000001","scope_revision_id":"SCR-0000000000000001","scope_revision_hash":"1"*64,"status":"ESTIMATING"}
        self.gate = None
    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        if "FROM landscape_v4_runs" in normalized:
            return Cursor(self.run if params[0] == self.run["run_id"] else None)
        if normalized.startswith("INSERT INTO landscape_v4_scale_gates"):
            if self.gate is None:
                keys = ("run_id","scope_revision_id","scope_revision_hash","publication_start","publication_end","query_count","estimated_total_results","estimated_total_pages","estimated_shards","tier","decision","created_at","decided_at")
                self.gate = dict(zip(keys, params, strict=True))
            return Cursor()
        if "FROM landscape_v4_scale_gates" in normalized: return Cursor(self.gate)
        if normalized.startswith("UPDATE landscape_v4_scale_gates"):
            if self.gate["decision"] is None:
                self.gate["decision"], self.gate["decided_at"] = params[0], params[1]
            return Cursor()
        if normalized.startswith("UPDATE landscape_v4_runs"):
            target = params[0]
            if "status='ESTIMATING'" in normalized and self.run["status"] == "ESTIMATING": self.run["status"] = target
            if "status='AWAITING_SCALE_CONFIRMATION'" in normalized and self.run["status"] == "AWAITING_SCALE_CONFIRMATION": self.run["status"] = target
            return Cursor()
        raise AssertionError(normalized)


def estimate(total):
    tier = ScaleTier.WITHIN_DEFAULT if total <= 500 else ScaleTier.CONFIRM_MEDIUM if total <= 1000 else ScaleTier.CONFIRM_LARGE
    return ScaleEstimate(scope_revision_id="SCR-0000000000000001", scope_revision_hash="1"*64, publication_start=date(2000,1,1), publication_end=date(2026,12,31), query_count=2, estimated_total_results=total, estimated_total_pages=(total+99)//100, estimated_shards=(total+199)//200, tier=tier)


class LandscapeScaleGateRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.connection = Connection()
        @contextmanager
        def connect(): yield self.connection
        self.repository = PostgreSQLScaleGateRepository("postgresql://fixture", connect=connect)

    def test_small_estimate_is_auto_approved_and_ready(self):
        record = self.repository.record(self.connection.run["run_id"], estimate(500))
        self.assertEqual(record.decision, ScaleDecision.APPROVED)
        self.assertEqual(self.connection.run["status"], "READY")
        self.assertEqual(self.repository.record(self.connection.run["run_id"], estimate(500)), record)

    def test_medium_waits_for_one_immutable_decision(self):
        record = self.repository.record(self.connection.run["run_id"], estimate(501))
        self.assertIsNone(record.decision)
        self.assertEqual(self.connection.run["status"], "AWAITING_SCALE_CONFIRMATION")
        approved = self.repository.decide(self.connection.run["run_id"], approve=True)
        self.assertEqual(approved.decision, ScaleDecision.APPROVED)
        self.assertEqual(self.connection.run["status"], "READY")
        with self.assertRaisesRegex(ScaleGatePersistenceError, "immutable"):
            self.repository.decide(self.connection.run["run_id"], approve=False)

    def test_scope_mismatch_fails_before_gate_write(self):
        value = estimate(10).model_copy(update={"scope_revision_hash":"2"*64})
        with self.assertRaisesRegex(ScaleGatePersistenceError, "Run scope"):
            self.repository.record(self.connection.run["run_id"], value)
        self.assertIsNone(self.connection.gate)


if __name__ == "__main__": unittest.main()
