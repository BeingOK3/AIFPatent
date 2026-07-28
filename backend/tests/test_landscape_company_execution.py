from __future__ import annotations

import asyncio
import unittest
from datetime import date
from types import SimpleNamespace

from idea.providers.base import FetchedDocument
from landscape.company_assignment import CompanyAssignmentResult
from landscape.company_classification import COMPANY_CLASSIFIER_NAME
from landscape.company_profiles import COMPANY_PROFILE_AGENT_NAME
from landscape.execution import LandscapeExecutionService
from landscape.schemas import (
    AnalysisMode,
    CompanyTechnologyProfileNarrative,
    LandscapeCoverageAudit,
    LandscapeScope,
)
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
        self.trend_analysis = None
        self.audits = []
        self.put_calls = 0
        self.repair_profile_calls = []
        self.trend_put_calls = 0
        self.repair_trend_calls = []

    def list_company_assignments(self, _run_id):
        return self.assignment_result

    def list_candidates(self, _run_id):
        return [
            {"publication_number": publication}
            for publication in sorted(self.analyses)
        ]

    def list_patent_analyses(self, _run_id):
        return dict(self.analyses)

    def list_company_profiles(self, _run_id):
        return dict(self.profiles)

    def put_company_profile(self, _run_id, *, company_id, profile):
        self.put_calls += 1
        self.profiles[company_id] = profile
        return profile

    def put_repaired_company_profile(
        self, _run_id, *, repair_round, company_id, profile
    ):
        self.repair_profile_calls.append((repair_round, company_id))
        self.profiles[company_id] = profile
        return profile

    def list_fetched_documents(self, _run_id):
        return {
            publication: FetchedDocument(
                provider="fixture",
                publication_number=publication,
                publication_date="2026-05-01",
                url=f"https://example.test/{publication}",
            )
            for publication in self.analyses
        }

    def list_cross_company_analysis(self, _run_id):
        return self.trend_analysis

    def put_cross_company_analysis(self, _run_id, analysis):
        self.trend_put_calls += 1
        self.trend_analysis = analysis
        return analysis

    def put_repaired_cross_company_analysis(self, _run_id, *, repair_round, analysis):
        self.repair_trend_calls.append(repair_round)
        self.trend_analysis = analysis
        return analysis

    def list_coverage_audits(self, _run_id):
        return list(self.audits)

    def put_coverage_audit(self, _run_id, *, repair_round, audit):
        self.audits.append(audit)
        return audit


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
            candidate_repository=self.repository,
            company_repository=self.repository,
            fetch_repository=self.repository,
            analysis_repository=self.repository,
            profile_repository=self.repository,
            trend_repository=self.repository,
            audit_repository=self.repository,
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

    def test_repair_forces_append_only_company_and_trend_snapshots(self) -> None:
        asyncio.run(self.service.analyze_company("run-1", "CO-HUAWEI"))
        repaired = asyncio.run(
            self.service.analyze_company(
                "run-1", "CO-HUAWEI", repair_round=1
            )
        )
        self.assertFalse(repaired["recovered"])
        self.assertEqual(self.repository.repair_profile_calls, [(1, "CO-HUAWEI")])
        self.service.scope = lambda _run_id: LandscapeScope(  # type: ignore[method-assign]
            mode=AnalysisMode.TECHNOLOGY,
            technology_direction="液冷",
            publication_start=date(2026, 4, 1),
            publication_end=date(2026, 6, 30),
        )
        asyncio.run(self.service.analyze_cross_company_trends("run-1"))
        trend = asyncio.run(
            self.service.analyze_cross_company_trends("run-1", repair_round=1)
        )
        self.assertFalse(trend["recovered"])
        self.assertEqual(self.repository.repair_trend_calls, [1])

    def test_repair_executor_runs_only_planned_profile_and_trend_work(self) -> None:
        self.service.scope = lambda _run_id: LandscapeScope(  # type: ignore[method-assign]
            mode=AnalysisMode.TECHNOLOGY,
            technology_direction="液冷",
            publication_start=date(2026, 4, 1),
            publication_end=date(2026, 6, 30),
        )
        result = asyncio.run(
            self.service.repair_gaps(
                "run-1",
                repair_round=1,
                audit=LandscapeCoverageAudit(
                    decision="REPAIR",
                    coverage_ratio=0.5,
                    missing_publications=["CN1A"],
                    repair_targets=["PROFILE:CO-HUAWEI"],
                ),
            )
        )
        self.assertEqual(result["fetched_publications"], [])
        self.assertEqual(result["analyzed_publications"], [])
        self.assertEqual(result["rebuilt_company_ids"], ["CO-HUAWEI"])
        self.assertTrue(result["trend_rebuilt"])
        self.assertEqual(self.repository.repair_profile_calls, [(1, "CO-HUAWEI")])
        self.assertEqual(self.repository.repair_trend_calls, [1])

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

    def test_single_company_trend_is_persisted_and_resumed_without_model(self):
        asyncio.run(self.service.analyze_company("run-1", "CO-HUAWEI"))
        calls_before_trend = len(self.model.calls)
        self.service.scope = lambda _run_id: LandscapeScope(  # type: ignore[method-assign]
            mode=AnalysisMode.TECHNOLOGY,
            technology_direction="液冷",
            publication_start=date(2026, 4, 1),
            publication_end=date(2026, 6, 30),
        )

        first = asyncio.run(
            self.service.analyze_cross_company_trends("run-1")
        )
        second = asyncio.run(
            self.service.analyze_cross_company_trends("run-1")
        )

        self.assertFalse(first["recovered"])
        self.assertTrue(second["recovered"])
        self.assertEqual(first["trend_count"], 0)
        self.assertEqual(self.repository.trend_put_calls, 1)
        self.assertEqual(len(self.model.calls), calls_before_trend)

    def test_coverage_audit_pass_is_persisted_and_resumed(self):
        asyncio.run(self.service.analyze_company("run-1", "CO-HUAWEI"))
        self.service.scope = lambda _run_id: LandscapeScope(  # type: ignore[method-assign]
            mode=AnalysisMode.TECHNOLOGY,
            technology_direction="液冷",
            publication_start=date(2026, 4, 1),
            publication_end=date(2026, 6, 30),
        )
        asyncio.run(self.service.analyze_cross_company_trends("run-1"))

        first = asyncio.run(self.service.verify_coverage("run-1"))
        second = asyncio.run(self.service.verify_coverage("run-1"))

        self.assertEqual(first["decision"], "PASS")
        self.assertFalse(first["recovered"])
        self.assertTrue(second["recovered"])
        self.assertEqual(len(self.repository.audits), 1)

    def test_coverage_audit_executes_one_bounded_repair_then_reaudits(self):
        self.service.scope = lambda _run_id: LandscapeScope(  # type: ignore[method-assign]
            mode=AnalysisMode.TECHNOLOGY,
            technology_direction="液冷",
            publication_start=date(2026, 4, 1),
            publication_end=date(2026, 6, 30),
        )
        first = asyncio.run(self.service.verify_coverage("run-1"))
        result = asyncio.run(self.service.repair_coverage_gaps("run-1"))

        self.assertEqual(first["decision"], "REPAIR")
        self.assertEqual(result["decision"], "PASS")
        self.assertEqual(result["repair_round"], 1)
        self.assertIsNotNone(result["repair"])
        self.assertEqual(len(self.repository.audits), 2)
        self.assertEqual(self.repository.audits[0].decision, "REPAIR")
        self.assertEqual(self.repository.audits[1].decision, "PASS")


if __name__ == "__main__":
    unittest.main()
