from __future__ import annotations

import unittest
from types import SimpleNamespace

from idea.providers.base import FetchedDocument, PageStopReason, SearchHit, SearchPage
from landscape.analytics_assembly import assemble_metric_units
from landscape.classification_terminal import (
    ClassificationAction,
    ClassificationResult,
    ClassificationTerminal,
)
from landscape.direction_record import DirectionRecord, DirectionStatus
from landscape.family_resolution import resolve_families
from landscape.metrics import build_metric_cube
from landscape.organization_assignment import assign_organizations
from landscape.others_discovery import discover_others_directions
from landscape.patent_snapshot import make_snapshot_set, snapshot_bibliography, snapshot_from_document
from landscape.publication_freeze import freeze_publications
from landscape.query_planning import build_query_plan
from landscape.report_v4 import REPORT_SCHEMA_VERSION, build_report_v4, render_report_markdown
from landscape.representatives import select_representative_patents
from landscape.scale_gate import estimate_scale
from landscape.scale_repository import ScaleDecision, ScaleGateRecord
from landscape.scope import NameLanguage
from landscape.taxonomy import compile_taxonomy_markdown
from landscape.trends import build_trend_candidates
from tests.test_landscape_query_planning import confirmed_scope


class LandscapeReportV4Tests(unittest.TestCase):
    def test_programmatic_report_is_complete_hashed_and_uses_safe_links(self):
        taxonomy = compile_taxonomy_markdown(
            """| 一级分类 | 二级分类 | 三级分类 |
| --- | --- | --- |
| Hardware | Cooling | Liquid |
"""
        )
        scope = confirmed_scope(
            technology_terms=(("液冷", NameLanguage.ZH), ("liquid cooling", NameLanguage.EN))
        )
        plan = build_query_plan(scope)
        hit = SearchHit(
            provider="fixture",
            provider_rank=1,
            publication_number="US1234567A1",
            title="Liquid cooling patent",
            url="https://example.test/US1234567A1",
            publication_date="2024-06-01",
            assignee="Acme Ltd",
        )
        pages = {
            query.query_id: (
                SearchPage(
                    hits=[hit] if index == 0 else [],
                    page_number=1,
                    provider_request_id=f"REQ-{index}",
                    stop_reason=PageStopReason.QUERY_EXHAUSTED,
                    reported_total_results=1 if index == 0 else 0,
                ),
            )
            for index, query in enumerate(plan.queries)
        }
        frozen = freeze_publications(
            "LRN-0123456789abcdef", ((plan.queries[0].query_id, hit),)
        )
        snapshot = snapshot_from_document(
            frozen.publications[0],
            FetchedDocument(
                provider="details",
                publication_number="US1234567A1",
                title="Liquid cooling patent",
                assignees=["Acme Ltd"],
                publication_date="2024-06-01",
                url="https://example.test/US1234567A1",
                abstract_text="A cold plate and liquid loop remove processor heat efficiently.",
            ),
        )
        snapshots = make_snapshot_set(frozen, (snapshot,))
        resolution = resolve_families(snapshot_bibliography(snapshots))
        unit_id = resolution.analysis_units[0].analysis_unit_id
        organizations = assign_organizations(
            frozen.run_id, scope, {snapshot.publication_id: snapshot.applicants}
        )
        direction = DirectionRecord(
            analysis_unit_id=unit_id,
            status=DirectionStatus.AVAILABLE,
            evidence_sufficient=True,
            solution_mechanism="通过冷板液体回路移除处理器热量",
            direction_summary="处理器冷板液冷方向",
            keywords=("冷板", "液冷"),
            confidence=0.8,
            evidence_ids=("EV-1",),
        )
        classification = ClassificationResult(
            analysis_unit_id=unit_id,
            action=ClassificationAction.NONE_OF_CANDIDATES,
            terminal=ClassificationTerminal.OTHERS,
            evidence_ids=("EV-1",),
            confidence=0.75,
            review_round=0,
        )
        others = discover_others_directions((classification,), (direction,))
        assembly = assemble_metric_units(
            resolution,
            snapshots,
            organizations,
            (classification,),
            (direction,),
            others,
            taxonomy,
        )
        cube = build_metric_cube(
            assembly.units,
            publication_start=scope.publication_start,
            publication_end=scope.publication_end,
        )
        trends = build_trend_candidates(cube, assembly.units)
        representatives = select_representative_patents(
            assembly.units, direction_id=assembly.units[0].direction_id
        )
        estimate = estimate_scale(plan, tuple(1 for _ in plan.queries))
        report = build_report_v4(
            run=SimpleNamespace(
                run_id=frozen.run_id,
                workflow_version="landscape-v4/1.0.0",
                taxonomy_version=taxonomy.taxonomy_version,
            ),
            scope=scope,
            query_plan=plan,
            search_pages_by_query=pages,
            scale_gate=ScaleGateRecord(
                run_id=frozen.run_id,
                estimate=estimate,
                decision=ScaleDecision.APPROVED,
                created_at=1,
                decided_at=1,
            ),
            resolution=resolution,
            snapshots=snapshots,
            organizations=organizations,
            classifications=(classification,),
            directions=(direction,),
            others=others,
            metric_cube=cube,
            trends=trends,
            representatives=representatives,
            limitations=(),
        )
        self.assertEqual(report.schema_version, REPORT_SCHEMA_VERSION)
        self.assertEqual(report.counts.frozen_publication_count, 1)
        self.assertEqual(report.counts.others_count, 1)
        self.assertEqual(report.patents[0].patent_url, "https://patents.google.com/patent/US1234567A1")
        serialized = report.model_dump_json()
        self.assertNotIn("api_key", serialized.casefold())
        markdown = render_report_markdown(report)
        self.assertIn("https://patents.google.com/patent/US1234567A1", markdown)
        self.assertIn("公开时间窗", markdown)


if __name__ == "__main__":
    unittest.main()
