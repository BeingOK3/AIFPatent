from __future__ import annotations

import unittest
from datetime import date

from pydantic import ValidationError

from landscape.api import LandscapeRuntimeRequest
from landscape.schemas import (
    AnalysisMode,
    AssigneeScope,
    CompetitorInput,
    LandscapeScope,
    PeriodPreset,
)


class LandscapeSchemaTests(unittest.TestCase):
    def test_runtime_model_url_rejects_accidentally_concatenated_urls(self) -> None:
        with self.assertRaisesRegex(ValidationError, "exactly one URL"):
            LandscapeRuntimeRequest.model_validate(
                {
                    "api_key": "test-token",
                    "base_url": (
                        "https://ark.cn-beijing.volces.com/api/coding/v3"
                        "https://ark.cn-beijing.volces.com/api/coding/v3"
                    ),
                    "model": "kimi-k2.6",
                }
            )

    def test_mode_is_derived_from_supplied_inputs(self) -> None:
        values = {
            "publication_start": date(2026, 4, 1),
            "publication_end": date(2026, 7, 1),
        }
        technology = LandscapeScope(technology_direction="液冷", **values)
        competitor = LandscapeScope(
            competitors=[CompetitorInput(name="Huawei")], **values
        )
        combined = LandscapeScope(
            technology_direction="液冷",
            competitors=[CompetitorInput(name="Huawei")],
            **values,
        )
        self.assertEqual(technology.mode, AnalysisMode.TECHNOLOGY)
        self.assertEqual(competitor.mode, AnalysisMode.COMPETITOR)
        self.assertEqual(combined.mode, AnalysisMode.TECHNOLOGY_COMPETITOR)

    def test_explicit_mode_must_match_derived_mode(self) -> None:
        with self.assertRaisesRegex(ValidationError, "mode does not match"):
            LandscapeScope(
                mode=AnalysisMode.COMPETITOR,
                technology_direction="液冷",
                publication_start=date(2026, 4, 1),
                publication_end=date(2026, 7, 1),
            )

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

    def test_assignee_scope_defaults_to_entity_and_accepts_explicit_group(self) -> None:
        self.assertEqual(
            CompetitorInput(name="华为").assignee_scope,
            AssigneeScope.ENTITY,
        )
        self.assertEqual(
            CompetitorInput(name="华为", assignee_scope="GROUP").assignee_scope,
            AssigneeScope.GROUP,
        )

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

    def test_period_preset_is_server_authoritative(self) -> None:
        scope = LandscapeScope(
            technology_direction="液冷",
            period_preset=PeriodPreset.QUARTER,
            publication_start=date(2026, 6, 21),
            publication_end=date(2026, 7, 22),
        )
        self.assertEqual(scope.publication_start, date(2026, 4, 22))

        month_end = LandscapeScope(
            technology_direction="液冷",
            period_preset=PeriodPreset.ONE_MONTH,
            publication_start=date(2026, 3, 1),
            publication_end=date(2026, 3, 31),
        )
        self.assertEqual(month_end.publication_start, date(2026, 2, 28))


if __name__ == "__main__":
    unittest.main()
