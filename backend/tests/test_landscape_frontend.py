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
        self.assertIn("TECHNOLOGY_COMPETITOR", self.javascript)
        self.assertIn("系统自动判定", self.javascript)
        self.assertNotIn('name="mode"', self.html)
        self.assertIn('id="technology-direction"', self.html)
        self.assertIn('id="competitors"', self.html)
        self.assertIn("自动识别专利申请人别名", self.html)

    def test_aliases_and_debug_are_rendered(self) -> None:
        for fragment in (
            "/api/landscape/runs/", "/debug", "searched_competitor_aliases",
            "本次检索到的友商别名", "系统运行调试", "provider_statuses",
            "provider_attempts", "technical_direction_expansion", "中英文技术词",
            "实际补全",
        ):
            self.assertIn(fragment, self.javascript if fragment != "系统运行调试" else self.html)
        self.assertIn("debug-view", self.css)

    def test_all_landscape_javascript_ids_exist_in_page(self) -> None:
        html_ids = set(re.findall(r'\bid="([^\"]+)"', self.html))
        referenced = set(re.findall(r'\$\("([A-Za-z][A-Za-z0-9_-]*)"\)', self.javascript))
        self.assertEqual(referenced - html_ids, set())

    def test_serpapi_key_is_not_sent_by_browser(self) -> None:
        self.assertIn("本地私密 JSON", self.html)
        self.assertNotIn('id="serpapi-api-key"', self.html)
        self.assertNotIn("serpapi_api_key", self.javascript)
        self.assertNotIn("localStorage", self.javascript)


if __name__ == "__main__":
    unittest.main()
