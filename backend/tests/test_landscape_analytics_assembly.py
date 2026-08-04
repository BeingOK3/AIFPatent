from __future__ import annotations

import unittest
from datetime import date

from landscape.analytics_assembly import assemble_metric_units
from landscape.classification_terminal import (
    ClassificationAction,
    ClassificationResult,
    ClassificationTerminal,
)
from landscape.direction_record import DirectionRecord, DirectionStatus
from landscape.family_resolution import resolve_families, BibliographicPublication
from landscape.organization_assignment import (
    Organization,
    OrganizationAssignmentSet,
    OrganizationType,
    PublicationOrganizationAssignment,
)
from landscape.others_discovery import discover_others_directions
from landscape.patent_snapshot import PatentSnapshot, PatentSnapshotSet, PatentSnapshotStatus
from landscape.taxonomy import compile_taxonomy_markdown


TAXONOMY = compile_taxonomy_markdown(
    """| 一级分类 | 二级分类 | 三级分类 |
| --- | --- | --- |
| Hardware | Cooling | Liquid |
"""
)


def snapshot(publication_id, number, publication_date):
    values = dict(
        publication_id=publication_id,
        status=PatentSnapshotStatus.AVAILABLE,
        publication_number=number,
        application_number=None,
        family_id=None,
        title=number,
        applicants=("Acme Ltd",),
        priority_date=None,
        filing_date=None,
        publication_date=publication_date,
        url=f"https://example.test/{number}",
        language="en",
        abstract_text="technical abstract",
        provider="details",
        failure_reason=None,
    )
    import hashlib, json
    semantic = {
        **values,
        "status": "AVAILABLE",
        "applicants": ["Acme Ltd"],
        "publication_date": publication_date.isoformat() if publication_date else None,
    }
    content_hash = hashlib.sha256(
        json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return PatentSnapshot(**values, content_hash=content_hash)


class AnalyticsAssemblyTests(unittest.TestCase):
    def test_missing_dates_are_explicitly_excluded_without_fabrication(self):
        publications = (
            BibliographicPublication(publication_id="PUB-0000000000000001", publication_number="US1A1"),
            BibliographicPublication(publication_id="PUB-0000000000000002", publication_number="US2A1"),
        )
        resolution = resolve_families(publications)
        snapshots = PatentSnapshotSet(
            run_id="RUN-1",
            snapshots=(
                snapshot("PUB-0000000000000001", "US1A1", date(2024, 1, 2)),
                snapshot("PUB-0000000000000002", "US2A1", None),
            ),
        )
        organization_id = "ORG-" + __import__("hashlib").sha256(b"acme ltd").hexdigest()[:16]
        organizations = OrganizationAssignmentSet(
            run_id="RUN-1",
            organizations=(
                Organization(
                    organization_id=organization_id,
                    display_name="Acme Ltd",
                    normalized_name="acme ltd",
                    organization_type=OrganizationType.COMPANY,
                    observed_names=("Acme Ltd",),
                ),
            ),
            assignments=tuple(
                PublicationOrganizationAssignment(
                    publication_id=item.publication_id,
                    primary_organization_id=organization_id,
                    observed_applicants=("Acme Ltd",),
                )
                for item in snapshots.snapshots
            ),
        )
        directions = tuple(
            DirectionRecord(
                analysis_unit_id=unit.analysis_unit_id,
                status=DirectionStatus.AVAILABLE,
                evidence_sufficient=True,
                solution_mechanism="液冷回路",
                direction_summary="液冷方向",
                confidence=0.8,
                evidence_ids=(f"EV-{index}",),
            )
            for index, unit in enumerate(resolution.analysis_units, start=1)
        )
        classifications = tuple(
            ClassificationResult(
                analysis_unit_id=item.analysis_unit_id,
                action=ClassificationAction.NONE_OF_CANDIDATES,
                terminal=ClassificationTerminal.OTHERS,
                confidence=0.7,
                review_round=0,
            )
            for item in directions
        )
        others = discover_others_directions(classifications, directions)
        assembly = assemble_metric_units(
            resolution, snapshots, organizations, classifications, directions, others, TAXONOMY
        )
        self.assertEqual(len(assembly.units), 1)
        self.assertEqual(len(assembly.excluded_missing_date_analysis_unit_ids), 1)
        self.assertEqual(assembly.units[0].publications[0].publication_date, date(2024, 1, 2))


if __name__ == "__main__":
    unittest.main()
