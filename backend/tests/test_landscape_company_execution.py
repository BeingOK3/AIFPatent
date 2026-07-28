from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from landscape.company_assignment import CompanyAssignmentResult
from landscape.company_classification import COMPANY_CLASSIFIER_NAME
from landscape.company_profiles import COMPANY_PROFILE_AGENT_NAME
from landscape.execution import LandscapeExecutionService
from landscape.schemas import CompanyTechnologyProfileNarrative
from tests.test_landscape_company_classification import company_batch
from tests.test_landscape_company_profiles import classification


class _CompanyExecutionModel:
    def __init__(self):
        self.calls = []

    async def complete(self, agent_name, *, system_prompt, input_payload):
        self.calls.append((agent_name, system_prompt, input_payload))
        if agent_name == COMPANY_CLASSIFIER_NAME:
            return SimpleNamespace(output=classification())
        if agent_name == COMPANY_PROFILE_AGENT_NAME:
            return SimpleNamespace(
                output=CompanyTechnologyProfileNarrative(
                    overall_summary="该公司布局换热结构与冷却控制。",
                    technology_directions=["换热结构", "控制策略"],
                    limitations=["仅基于当前时间窗。"],
                )
            )
        raise AssertionError(f"unexpected model agent: {agent_name}")


class _CompanyExecutionRepository:
    def __init__(self):
        batch = company_batch(["CN1A", "US2A1"])
        self.assignment_result = CompanyAssignmentResult(
            companies=(batch.company,),
            assignments=tuple(item.assignment for item in batch.items),
        )
        self.analyses = {
            item.publication_number: item.analysis for item in batch.items
        }
        self.profiles = {}
        self.put_calls = 0

    def list_company_assignments(self, _run_id):
        return self.assignment_result

    def list_patent_analyses(self, _run_id):
        return dict(self.analyses)

    def list_company_profiles(self, _run_id):
        return dict(self.profiles)

    def put_company_profile(self, _run_id, *, company_id, profile):
        self.put_calls += 1
        self.profiles[company_id] = profile
        return profile


class _CompanyFanoutRecorder:
    def __init__(self):
        self.calls = []

    async def execute(self, run_id, company_ids):
        self.calls.append((run_id, list(company_ids)))
        return list(company_ids)


class LandscapeCompanyExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.model = _CompanyExecutionModel()
        self.repository = _CompanyExecutionRepository()
        self.service = LandscapeExecutionService(
            database=None,  # type: ignore[arg-type]
            store=None,  # type: ignore[arg-type]
            model=self.model,  # type: ignore[arg-type]
            providers=[],
            provider_timeout_seconds={},
            analysis_concurrency=1,
            report_service=None,  # type: ignore[arg-type]
            company_repository=self.repository,
            analysis_repository=self.repository,
            profile_repository=self.repository,
        )

    def test_company_analysis_persists_once_and_resume_skips_model(self) -> None:
        first = asyncio.run(
            self.service.analyze_company("run-1", "CO-HUAWEI")
        )
        second = asyncio.run(
            self.service.analyze_company("run-1", "CO-HUAWEI")
        )

        self.assertFalse(first["recovered"])
        self.assertTrue(second["recovered"])
        self.assertEqual(first["publication_count"], 2)
        self.assertEqual(first["category_count"], 2)
        self.assertEqual(self.repository.put_calls, 1)
        self.assertEqual(
            [call[0] for call in self.model.calls],
            [COMPANY_CLASSIFIER_NAME, COMPANY_PROFILE_AGENT_NAME],
        )

    def test_unknown_company_fails_before_model_call(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown or empty"):
            asyncio.run(self.service.analyze_company("run-1", "CO-MISSING"))
        self.assertEqual(self.model.calls, [])
        self.assertEqual(self.repository.put_calls, 0)

    def test_recovered_profile_is_revalidated_against_current_batch(self) -> None:
        asyncio.run(self.service.analyze_company("run-1", "CO-HUAWEI"))
        stored = self.repository.profiles["CO-HUAWEI"]
        self.repository.profiles["CO-HUAWEI"] = stored.model_copy(
            update={
                "technology_categories": stored.technology_categories[:1]
            }
        )
        calls_before_resume = len(self.model.calls)

        with self.assertRaisesRegex(Exception, "cover the batch exactly"):
            asyncio.run(self.service.analyze_company("run-1", "CO-HUAWEI"))
        self.assertEqual(len(self.model.calls), calls_before_resume)

    def test_main_company_stage_dispatches_only_nonempty_analysis_batches(self):
        fanout = _CompanyFanoutRecorder()
        self.service.bind_company_fanout(fanout)

        result = asyncio.run(self.service.analyze_companies("run-1"))

        self.assertEqual(
            fanout.calls,
            [("run-1", ["CO-HUAWEI"])],
        )
        self.assertEqual(result["company_count"], 1)
        self.assertEqual(result["completed_company_ids"], ["CO-HUAWEI"])
        with self.assertRaisesRegex(RuntimeError, "already bound"):
            self.service.bind_company_fanout(fanout)


if __name__ == "__main__":
    unittest.main()
