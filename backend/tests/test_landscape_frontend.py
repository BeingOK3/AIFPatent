from __future__ import annotations

import re
import unittest
from pathlib import Path


class LandscapeFrontendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = Path("frontend/landscape.html").read_text(encoding="utf-8")
        cls.javascript = Path("frontend/landscape.js").read_text(encoding="utf-8")
        cls.css = Path("frontend/landscape.css").read_text(encoding="utf-8")

    def test_mode_is_input_derived_and_both_inputs_remain_available(self) -> None:
        self.assertIn('id="derived-mode"', self.html)
        self.assertIn("COMPANY_AND_TECHNOLOGY", self.javascript)
        self.assertIn("TECHNOLOGY_ONLY", self.javascript)
        self.assertIn("COMPANY_ONLY", self.javascript)
        self.assertIn("系统自动判定", self.javascript)
        self.assertNotIn('name="mode"', self.html)
        self.assertIn('id="technology-direction"', self.html)
        self.assertIn('id="competitors"', self.html)

    def test_scope_is_persisted_expanded_reviewed_and_confirmed_before_search(self) -> None:
        for fragment in (
            "/api/landscape/scope-drafts",
            "/expand",
            "/confirm",
            "expected_revision",
            "AWAITING_CONFIRMATION",
            "scopeDraft",
            "restoreScopeDraft",
            "EXCLUDED:NONE",
            "EXCLUDED:REJECT",
            "EXCLUDED:RETIRE",
            "USER_ADDED",
            'jsonRequest("/api/landscape/runs"',
            "scope_revision_id",
            "/credentials",
        ):
            self.assertIn(fragment, self.javascript)
        self.assertIn('id="scope-review-panel"', self.html)
        self.assertIn('id="confirm-scope"', self.html)

    def test_v4_progress_scale_gate_and_debug_are_rendered(self) -> None:
        for fragment in (
            "/api/landscape/runs/", "/debug", "stage_name",
            "系统运行调试", "query_plan", "scale_gate", "规模闸门",
            "/scale-decision", "WAITING_FOR_CREDENTIALS", "attachCredentials",
        ):
            self.assertIn(fragment, self.javascript if fragment != "系统运行调试" else self.html)
        self.assertIn('id="scale-gate-actions"', self.html)
        self.assertIn('id="credential-actions"', self.html)
        self.assertIn("debug-view", self.css)

    def test_all_landscape_javascript_ids_exist_in_page(self) -> None:
        html_ids = set(re.findall(r'\bid="([^\"]+)"', self.html))
        referenced = set(re.findall(r'\$\("([A-Za-z][A-Za-z0-9_-]*)"\)', self.javascript))
        self.assertEqual(referenced - html_ids, set())

    def test_serpapi_key_is_not_sent_by_browser(self) -> None:
        self.assertIn("不写入草稿、数据库、日志或浏览器存储", self.html)
        self.assertNotIn('id="serpapi-api-key"', self.html)
        self.assertNotIn("serpapi_api_key", self.javascript)
        self.assertNotIn("localStorage", self.javascript)

    def test_time_range_keeps_inclusive_dates_and_has_no_twelve_month_cap(self) -> None:
        self.assertIn("TEN_YEARS: 120", self.javascript)
        self.assertIn("getUTCFullYear", self.javascript)
        self.assertIn("getUTCDate", self.javascript)
        self.assertIn("公开日区间（含起止日）", self.html)
        self.assertIn('value="CUSTOM"', self.html)
        self.assertNotIn('max="12"', self.html)

    def test_report_4_renders_auditable_results_without_deep_read(self) -> None:
        for fragment in (
            "landscape-report/4.0.0", "metric_cube", "mode_view",
            "counting_disclosure", "query_audit", "representatives",
            "classification_terminal", "Unresolved", "Others",
            'target="_blank"', 'rel="noopener noreferrer"',
        ):
            self.assertIn(fragment, self.javascript)
        self.assertNotIn("deep-analyze", self.javascript)
        self.assertNotIn("deep_read", self.javascript)


if __name__ == "__main__":
    unittest.main()
