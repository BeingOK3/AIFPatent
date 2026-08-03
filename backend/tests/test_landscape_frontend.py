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
        ):
            self.assertIn(fragment, self.javascript)
        self.assertIn('id="scope-review-panel"', self.html)
        self.assertIn('id="confirm-scope"', self.html)
        self.assertNotIn('jsonRequest("/api/landscape/runs", { method: "POST"', self.javascript)

    def test_aliases_and_debug_are_rendered(self) -> None:
        for fragment in (
            "/api/landscape/runs/", "/debug", "searched_competitor_aliases",
            "本次检索到的友商别名", "系统运行调试", "provider_statuses",
            "provider_attempts", "technical_direction_expansion", "中英文技术词",
            "日期详情补全",
            "/deep-analyze",
            "开始精读已选",
            "待发起精读",
            "deep_read",
        ):
            self.assertIn(fragment, self.javascript if fragment != "系统运行调试" else self.html)
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

    def test_company_trend_report_renders_company_data_and_hides_legacy_clusters(self) -> None:
        for fragment in (
            "company_patent_counts",
            "各公司专利族数量",
            "统计口径：时间与友商条件过滤后",
            "patent.family_status",
            "item.family_footprint",
            "analysis_selection",
            "公司覆盖",
            "全族状态",
            "overall_legal_status",
            "company_profiles",
            "cross_company_analysis",
            "公司技术画像",
            "跨公司整体技术趋势",
            "company_trend_coverage",
            "company_trend_coverage_history",
            "公司趋势覆盖审计",
            "landscape-report/2.0.0",
            "历史技术聚类",
        ):
            self.assertIn(fragment, self.javascript)
        self.assertNotIn("申请日趋势", self.javascript)


if __name__ == "__main__":
    unittest.main()
