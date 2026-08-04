from __future__ import annotations

import unittest

from idea.providers.base import FetchedDocument, SearchHit
from landscape.family_resolution import resolve_families
from landscape.patent_snapshot import (
    PatentSnapshotError,
    failed_snapshot,
    make_snapshot_set,
    snapshot_bibliography,
    snapshot_from_document,
)
from landscape.publication_freeze import freeze_publications


class LandscapePatentSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.frozen = freeze_publications(
            "RUN-1",
            tuple(
                (
                    "Q-1",
                    SearchHit(
                        provider="search",
                        provider_rank=index,
                        publication_number=publication,
                        url=f"https://example.test/{publication}",
                    ),
                )
                for index, publication in enumerate(("US1A1", "US1B2"), start=1)
            ),
        )

    def test_snapshot_keeps_abstract_bibliography_and_all_applicants_only(self):
        document = FetchedDocument(
            provider="details",
            publication_number="US1A1",
            application_number="US10/001",
            title="A title",
            assignee="First Corp",
            assignees=["First Corp", "Joint University"],
            publication_date="2024-01-03",
            url="https://example.test/US1A1",
            abstract_text="An abstract that is sufficiently descriptive.",
            claims_text="SECRET CLAIM TEXT",
            description_text="SECRET DESCRIPTION TEXT",
        )
        snapshot = snapshot_from_document(self.frozen.publications[0], document)
        payload = snapshot.model_dump(mode="json")
        self.assertEqual(snapshot.applicants, ("First Corp", "Joint University"))
        self.assertIn("abstract_text", payload)
        self.assertNotIn("claims_text", payload)
        self.assertNotIn("description_text", payload)
        self.assertNotIn("SECRET", repr(payload))

    def test_provider_identity_mismatch_fails_closed(self):
        document = FetchedDocument(
            provider="details",
            publication_number="US999A1",
            url="https://example.test/US999A1",
        )
        with self.assertRaisesRegex(PatentSnapshotError, "provider returned"):
            snapshot_from_document(self.frozen.publications[0], document)

    def test_failed_fetch_remains_a_singleton_and_set_is_exact(self):
        first = snapshot_from_document(
            self.frozen.publications[0],
            FetchedDocument(
                provider="details",
                publication_number="US1A1",
                application_number="US10/001",
                url="https://example.test/US1A1",
            ),
        )
        second = failed_snapshot(
            self.frozen.publications[1], provider="details", reason="TIMEOUT"
        )
        snapshot_set = make_snapshot_set(self.frozen, (second, first))
        resolution = resolve_families(snapshot_bibliography(snapshot_set))
        self.assertEqual(resolution.publication_count, 2)
        self.assertEqual(resolution.analysis_unit_count, 2)
        with self.assertRaisesRegex(PatentSnapshotError, "exactly partition"):
            make_snapshot_set(self.frozen, (first,))


if __name__ == "__main__":
    unittest.main()
