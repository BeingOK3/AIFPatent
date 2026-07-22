from __future__ import annotations

import unittest
from datetime import date

from pydantic import ValidationError

from landscape.schemas import AnalysisMode, CompetitorInput, LandscapeScope


class LandscapeSchemaTests(unittest.TestCase):
    def test_technology_mode_requires_direction(self) -> None:
        with self.assertRaises(ValidationError):
            LandscapeScope(
                mode=AnalysisMode.TECHNOLOGY,
                publication_start=date(2026, 4, 1),
                publication_end=date(2026, 7, 1),
            )

    def test_competitor_mode_requires_confirmed_competitor(self) -> None:
        with self.assertRaises(ValidationError):
            LandscapeScope(
                mode=AnalysisMode.COMPETITOR,
                publication_start=date(2026, 4, 1),
                publication_end=date(2026, 7, 1),
            )

    def test_aliases_are_trimmed_and_deduplicated(self) -> None:
        competitor = CompetitorInput(
            name="Huawei",
            aliases=[" 华为 ", "HUAWEI", "华为", ""],
        )
        self.assertEqual(competitor.aliases, ["华为", "HUAWEI"])
        self.assertEqual(competitor.confirmed_names(), ("huawei", "华为"))

    def test_publication_window_is_inclusive_and_bounded(self) -> None:
        scope = LandscapeScope(
            mode=AnalysisMode.TECHNOLOGY,
            technology_direction="液冷数据中心",
            publication_start=date(2026, 7, 1),
            publication_end=date(2026, 7, 1),
        )
        self.assertEqual(scope.publication_start, scope.publication_end)
        with self.assertRaises(ValidationError):
            LandscapeScope(
                mode=AnalysisMode.TECHNOLOGY,
                technology_direction="液冷",
                publication_start=date(2025, 1, 1),
                publication_end=date(2026, 7, 1),
            )


if __name__ == "__main__":
    unittest.main()
