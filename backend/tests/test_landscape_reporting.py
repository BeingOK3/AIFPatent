from __future__ import annotations

import unittest

from idea.providers.base import FetchedDocument
from landscape.reporting import build_report, render_markdown, render_patents_csv
from landscape.schemas import (
    LandscapeCluster,
    LandscapeClusterPlan,
    LandscapeEvidenceRef,
    LandscapePatentAnalysis,
)


class LandscapeReportingTests(unittest.TestCase):
    def test_v2_report_uses_unique_company_counts_and_enriches_cluster_and_family(self) -> None:
        document = FetchedDocument(
            provider="serpapi_google_patents",
            publication_number="US123A1",
            application_number="US18/123456",
            family_id="family-123",
            title="Liquid cooled server rack",
            assignee="Example Corp Holdings",
            filing_date="2025-02-01",
            publication_date="2026-06-01",
            url="https://patents.google.com/patent/US123A1/en",
            raw_metadata={
                "worldwide_applications": {
                    "2025": [
                        {
                            "country_code": "US",
                            "application_number": "US18/123456",
                            "filing_date": "2025-02-01",
                            "legal_status": "ACTIVE",
                        },
                        {
                            "country_code": "EP",
                            "application_number": "EP25123456",
                            "filing_date": "2025-03-01",
                            "legal_status": "LAPSED",
                        },
                    ]
                }
            },
        )
        analysis = LandscapePatentAnalysis(
            publication_number="US123A1",
            prior_art="Air cooling is inefficient.",
            prior_art_problems=["High fan power"],
            core_invention_points=["Cold plate loop"],
            technical_problems_solved=["Rack heat removal"],
            beneficial_effects=["Lower cooling power"],
            technical_keywords=["cold plate"],
            evidence_refs=[
                LandscapeEvidenceRef(
                    evidence_id="EV-1",
                    supports=["core_invention_point"],
                )
            ],
        )
        clusters = LandscapeClusterPlan(
            clusters=[
                LandscapeCluster(
                    cluster_id="CL-1",
                    name="Cold plate",
                    summary="Direct-to-chip cooling.",
                    publication_numbers=["US123A1"],
                )
            ]
        )
        coverage = {
            "unique_candidate_count": 3,
            "unique_family_count": 3,
            "unique_publication_count": 5,
            "company_patent_counts": [
                {
                    "company": "Example Corp",
                    "patent_count": 3,
                    "share": 1.0,
                    "source": "CONFIRMED_COMPETITOR",
                }
            ],
        }
        report = build_report(
            run={
                "run_id": "run-1",
                "scope_json": {"mode": "COMPETITOR"},
                "model": "fixture",
            },
            coverage=coverage,
            documents={"US123A1": document},
            analyses={"US123A1": analysis},
            clusters=clusters,
            failures={},
            limitations=[],
            searched_competitor_aliases=[
                {
                    "primary_name": "Example Corp",
                    "aliases": ["Example"],
                    "searched_aliases": ["Example"],
                    "source": "MODEL_INFERRED",
                }
            ],
        )

        self.assertEqual(report["schema_version"], "landscape-report/1.2.0")
        self.assertNotIn("filing_date_trend", report["summary"])
        self.assertEqual(report["summary"]["family_count"], 3)
        self.assertEqual(report["summary"]["publication_count"], 5)
        self.assertEqual(report["summary"]["company_patent_counts"], coverage["company_patent_counts"])
        self.assertEqual(report["clusters"][0]["members"][0]["competitor"], "Example Corp")
        self.assertEqual(report["clusters"][0]["members"][0]["filing_date"], "2025-02-01")
        family = report["patents"][0]["family_status"]
        self.assertEqual(family["data_status"], "PARTIAL")
        self.assertEqual(family["overall_legal_status"], "MIXED")
        self.assertEqual(family["jurisdictions"], ["EP", "US"])
        self.assertEqual(len(family["members"]), 2)
        self.assertTrue(family["members"][1]["is_current_application"])

        markdown = render_markdown(report)
        self.assertIn("## 公司专利族数量", markdown)
        self.assertNotIn("申请日趋势", markdown)
        self.assertIn("US123A1｜Example Corp｜申请日 2025-02-01", markdown)
        self.assertIn("全族总体状态：MIXED", markdown)
        csv_content = render_patents_csv(report)
        self.assertIn("family_overall_legal_status", csv_content)
        self.assertIn("EP25123456|EP|LAPSED", csv_content)


if __name__ == "__main__":
    unittest.main()
