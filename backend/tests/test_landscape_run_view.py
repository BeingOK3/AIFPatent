from __future__ import annotations

import json
import unittest
from datetime import date
from types import SimpleNamespace

from landscape.api import _run_view
from landscape.stage_repository import (
    V4RunLimitation,
    V4StageName,
    V4StageRecord,
    V4StageStatus,
)


class StubCredentialVault:
    def has_credentials(self, run_id: str) -> bool:
        return False


class LandscapeRunViewSerializationTests(unittest.TestCase):
    def test_run_view_with_stages_and_limitations_is_json_serializable(self) -> None:
        run_id = "LRN-0000000000000001"
        run = SimpleNamespace(
            run_id=run_id,
            status="RUNNING",
            mode="COMPANY_ONLY",
            scope_revision_id="SCR-0000000000000001",
            taxonomy_version="TAX-0000000000000001",
            publication_start=date(2026, 1, 1),
            publication_end=date(2026, 12, 31),
            created_at=1,
            updated_at=2,
            started_at=3,
            completed_at=None,
            error_code=None,
            error_message=None,
        )
        stage = V4StageRecord(
            run_id=run_id,
            stage_name=V4StageName.FREEZE_PUBLICATIONS,
            stage_order=1,
            status=V4StageStatus.SUCCEEDED,
            attempt=1,
            completed_count=2,
            total_count=2,
            updated_at=4,
        )
        limitation = V4RunLimitation(
            run_id=run_id,
            limitation_id="LIM-0000000000000001",
            stage_name=V4StageName.FREEZE_PUBLICATIONS,
            code="PUBLICATION_DATE_OUTSIDE_WINDOW",
            message="some hits were excluded",
            affected_count=1,
            created_at=4,
        )
        runtime = SimpleNamespace(
            run_repository=SimpleNamespace(get=lambda _rid: run),
            scope_repository=SimpleNamespace(
                get_confirmed=lambda _sid: SimpleNamespace(
                    model_dump=lambda **_: {"scope_revision_id": run.scope_revision_id}
                )
            ),
            stage_repository=SimpleNamespace(
                list=lambda _rid: (stage,),
                limitations=lambda _rid: (limitation,),
            ),
            scale_repository=SimpleNamespace(
                get=lambda _rid: SimpleNamespace(
                    model_dump=lambda **_: {"estimate": {"estimated_total_results": 10}}
                )
            ),
            credential_vault=StubCredentialVault(),
        )

        view = _run_view(runtime, run_id)
        encoded = json.dumps(view, ensure_ascii=False, sort_keys=True)
        self.assertIn('"steps"', encoded)
        self.assertIn("PUBLICATION_DATE_OUTSIDE_WINDOW", encoded)
        self.assertEqual(len(view["progress"]["steps"]), 1)
        self.assertEqual(view["scale_gate"]["estimate"]["estimated_total_results"], 10)


if __name__ == "__main__":
    unittest.main()
