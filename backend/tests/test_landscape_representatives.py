from __future__ import annotations

import unittest
from datetime import date

from landscape.metrics import MetricAnalysisUnit, MetricPublication
from landscape.representatives import (
    PatentLinkStatus,
    RepresentativeExplanationProposal,
    apply_representative_explanation,
    deterministic_representative_explanation,
    google_patents_url,
    select_representative_patents,
)


def unit(
    index: int,
    *,
    direction: str = "DIR-A",
    organization: str = "ORG-A",
    year: int = 2024,
    confidence: float = 0.8,
    publication_number: str | None = None,
) -> MetricAnalysisUnit:
    return MetricAnalysisUnit(
        analysis_unit_id=f"AU-{index:016x}",
        direction_id=direction,
        classification_path=("一级", "二级"),
        publications=(
            MetricPublication(
                publication_id=f"PUB-{index}",
                publication_number=publication_number or f"CN10000{index}A",
                title=f"代表专利 {index}",
                publication_date=date(year, 6, 1),
                primary_organization_id=organization,
            ),
        ),
        classification_confidence=confidence,
        evidence_completeness=1,
        direction_centrality=0.8,
    )


class LandscapeRepresentativeTests(unittest.TestCase):
    def test_selection_is_direction_member_bound_deterministic_and_family_unique(self):
        units = (
            unit(1, confidence=0.9),
            unit(2, organization="ORG-B", year=2023, confidence=0.85),
            unit(3, direction="DIR-B", confidence=1),
        )
        first = select_representative_patents(
            units,
            direction_id="DIR-A",
            eligible_analysis_unit_ids=("AU-0000000000000001", "AU-0000000000000002"),
        )
        second = select_representative_patents(
            tuple(reversed(units)),
            direction_id="DIR-A",
            eligible_analysis_unit_ids=("AU-0000000000000002", "AU-0000000000000001"),
        )
        self.assertEqual(first, second)
        self.assertEqual({item.analysis_unit_id for item in first}, {
            "AU-0000000000000001", "AU-0000000000000002"
        })
        self.assertNotIn("AU-0000000000000003", {item.analysis_unit_id for item in first})
        self.assertEqual(len({item.analysis_unit_id for item in first}), len(first))

    def test_link_is_program_generated_and_has_safe_new_page_attributes(self):
        representative = select_representative_patents(
            (unit(1, publication_number="CN 123456 A"),),
            direction_id="DIR-A",
        )[0]
        self.assertEqual(
            representative.patent_url,
            "https://patents.google.com/patent/CN123456A",
        )
        self.assertEqual(representative.link_status, PatentLinkStatus.AVAILABLE)
        self.assertEqual(representative.link_target, "_blank")
        self.assertEqual(representative.link_rel, "noopener noreferrer")
        self.assertIsNone(google_patents_url("CN123/../../evil"))

    def test_invalid_publication_number_keeps_record_but_marks_link_unavailable(self):
        representative = select_representative_patents(
            (unit(1, publication_number="??"),),
            direction_id="DIR-A",
        )[0]
        self.assertEqual(representative.title, "代表专利 1")
        self.assertEqual(representative.link_status, PatentLinkStatus.UNAVAILABLE)
        self.assertIsNone(representative.patent_url)

    def test_model_can_explain_but_cannot_replace_selected_patent(self):
        representative = select_representative_patents(
            (unit(1),), direction_id="DIR-A"
        )[0]
        proposal = RepresentativeExplanationProposal(
            representative_id=representative.representative_id,
            analysis_unit_id=representative.analysis_unit_id,
            publication_id=representative.publication_id,
            explanation="该专利具有较完整摘要证据。",
        )
        self.assertEqual(
            apply_representative_explanation(representative, proposal).source,
            "MODEL",
        )
        with self.assertRaisesRegex(ValueError, "cannot change"):
            apply_representative_explanation(
                representative,
                proposal.model_copy(update={"publication_id": "PUB-invented"}),
            )
        fallback = deterministic_representative_explanation(representative)
        self.assertEqual(fallback.source, "DETERMINISTIC")
        self.assertIn("同一专利族", fallback.explanation)


if __name__ == "__main__":
    unittest.main()
