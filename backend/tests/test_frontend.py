from __future__ import annotations

import re
import unittest
from pathlib import Path


class FrontendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = Path("frontend/index.html").read_text(encoding="utf-8")
        cls.javascript = Path("frontend/app.js").read_text(encoding="utf-8")
        cls.css = Path("frontend/style.css").read_text(encoding="utf-8")

    def test_only_idea_workflow_is_visible_and_generic_agent_endpoint_is_absent(self) -> None:
        self.assertIn("AIFPatent", self.html)
        self.assertNotIn("AI4PATENT / INTERNAL", self.html)
        self.assertIn("IDEA 专利评估工作台", self.html)
        for legacy in ("5 大专利模块", "业界专利分析", "PCT申请评审", "侵权挖掘"):
            self.assertNotIn(legacy, self.html)
        self.assertNotIn('"/api/run"', self.javascript)
        self.assertIn("langgraph_node_started", self.javascript)

    def test_frontend_calls_durable_idea_history_progress_and_report_apis(self) -> None:
        for fragment in (
            "/api/idea/cases",
            "/events",
            "/report",
            "/rerun",
            "/cancel",
            "/debug",
        ):
            self.assertIn(fragment, self.javascript)
        for step in (
            "PREPARE_INPUT",
            "RETRIEVE_CANDIDATES",
            "ANALYZE_DOCUMENTS",
            "DETERMINE_NOVELTY",
            "AUDIT_AND_REPORT",
        ):
            self.assertIn(step, self.javascript)

    def test_all_javascript_dom_ids_exist_and_external_data_uses_text_content(self) -> None:
        html_ids = set(re.findall(r'\bid="([^"]+)"', self.html))
        referenced = set(re.findall(r'\$\("([A-Za-z][A-Za-z0-9_-]*)"\)', self.javascript))
        self.assertEqual(referenced - html_ids, set())
        self.assertNotIn(".innerHTML", self.javascript)

    def test_responsive_three_column_layout_and_budget_controls_exist(self) -> None:
        self.assertIn("grid-template-columns:", self.css)
        self.assertIn("@media (max-width: 760px)", self.css)
        for control in ("candidateMax", "deepMin", "deepMax", "searchMode"):
            self.assertIn(f'id="{control}"', self.html)
        self.assertIn('min="10"', self.html)

    def test_api_token_is_page_only_and_sent_for_create_and_rerun(self) -> None:
        self.assertRegex(
            self.html,
            r'id="apiToken"[^>]*type="password"[^>]*required[^>]*autocomplete="new-password"',
        )
        self.assertNotRegex(self.html, r'id="apiToken"[^>]*\bvalue=')
        self.assertIn('window.addEventListener("pageshow", clearRuntimeApiConfig)', self.javascript)
        self.assertIn('window.addEventListener("pagehide", clearRuntimeApiConfig)', self.javascript)
        for control in ("modelBaseUrl", "apiToken", "modelName"):
            self.assertIn(f'id="{control}"', self.html)
        self.assertIn("base_url: baseUrl", self.javascript)
        self.assertIn("api_key: apiKey", self.javascript)
        self.assertIn("resetWorkspace", self.javascript)
        self.assertIn("renderEmptyDebug", self.javascript)
        self.assertIn("patentLink", self.javascript)
        self.assertIn("`${score}/5`", self.javascript)
        self.assertIn("restoreRunSnapshot", self.javascript)
        self.assertIn("run.input_text", self.javascript)
        self.assertIn("run.input_hash", self.javascript)
        self.assertIn('$("caseTitle").readOnly = true', self.javascript)
        self.assertIn("不可变输入", self.html + self.javascript)
        self.assertIn("名称必须唯一", self.html)
        self.assertIn("reportHasLegacyEnglish", self.javascript)
        self.assertIn("chineseText", self.javascript)
        self.assertNotIn("localStorage", self.javascript)
        self.assertNotIn("sessionStorage", self.javascript)
        self.assertNotIn("/api/config", self.javascript)


if __name__ == "__main__":
    unittest.main()
