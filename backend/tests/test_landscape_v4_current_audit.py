from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
AUDIT_PATH = (
    ROOT
    / "backend"
    / "tests"
    / "fixtures"
    / "landscape_v4"
    / "current_implementation_audit.json"
)


class LandscapeV4CurrentImplementationAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.audit = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))

    def test_audit_schema_and_commit_are_explicit(self) -> None:
        self.assertEqual(
            self.audit["schema_version"], "landscape-current-audit/1.0.0"
        )
        self.assertRegex(self.audit["captured_after_commit"], r"^[0-9a-f]{7,40}$")

    def test_recorded_source_boundaries_exist(self) -> None:
        groups = (
            self.audit["replace_or_remove_modules"],
            self.audit["reuse_boundaries"],
            self.audit["frontend_entrypoints"],
        )
        for paths in groups:
            for relative_path in paths:
                with self.subTest(path=relative_path):
                    self.assertTrue((ROOT / relative_path).is_file())

    def test_source_contract_tokens_still_match_current_code(self) -> None:
        token_contracts = [
            *self.audit["production_entrypoints"],
            self.audit["legacy_limits"],
            self.audit["legacy_api"],
        ]
        for contract in token_contracts:
            source = (ROOT / contract["path"]).read_text(encoding="utf-8")
            for token in contract["required_tokens"]:
                with self.subTest(path=contract["path"], token=token):
                    self.assertIn(token, source)
        api_source = (ROOT / self.audit["legacy_api"]["path"]).read_text(
            encoding="utf-8"
        )
        for token in self.audit["legacy_api"]["removed_tokens"]:
            with self.subTest(removed_token=token):
                self.assertNotIn(token, api_source)

    def test_workflow_step_inventory_matches_source(self) -> None:
        source = (ROOT / self.audit["legacy_workflow"]["path"]).read_text(
            encoding="utf-8"
        )
        enum_block = source.split("class LandscapeWorkflowStep", 1)[1].split(
            "WORKFLOW_STEPS", 1
        )[0]
        declared = re.findall(r'^\s+[A-Z_]+ = "([A-Z_]+)"$', enum_block, re.MULTILINE)
        expected = [
            *self.audit["legacy_workflow"]["active_steps"],
            *self.audit["legacy_workflow"]["legacy_non_active_steps"],
        ]
        self.assertCountEqual(declared, expected)

    def test_landscape_migration_inventory_matches_repository(self) -> None:
        migration_root = ROOT / "deploy" / "rag" / "postgres-init"
        actual = sorted(path.name for path in migration_root.glob("07[0-5]_landscape_*.sql"))
        self.assertEqual(actual, self.audit["legacy_postgres_migrations"])
        self.assertEqual(actual[-1], "075_landscape_repair_snapshots.sql")


if __name__ == "__main__":
    unittest.main()
