from __future__ import annotations

import copy
import unittest

from landscape.taxonomy import compile_taxonomy_markdown
from landscape.taxonomy_repository import (
    TaxonomyPersistenceError,
    prepare_taxonomy_rows,
    validate_persisted_taxonomy,
)


SOURCE = """| 一级分类 | 二级分类 | 三级分类 |
| --- | --- | --- |
| 网络 | 路由 | SRv6 |
| 网络 | 交换 |  |
"""


class LandscapeTaxonomyRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.artifact = compile_taxonomy_markdown(SOURCE)
        self.version_row, prepared = prepare_taxonomy_rows(
            self.artifact, created_at=1234
        )
        self.category_rows = [
            {
                key: value
                for key, value in row.items()
                if key not in {"taxonomy_version", "created_at"}
            }
            for row in prepared
        ]

    def test_prepared_rows_are_complete_ordered_and_hashed(self) -> None:
        self.assertEqual(self.version_row["source_row_count"], 2)
        self.assertEqual(self.version_row["leaf_count"], 2)
        self.assertEqual(self.version_row["node_count"], len(self.artifact.nodes))
        self.assertEqual(
            [row["sort_order"] for row in self.category_rows],
            list(range(1, len(self.category_rows) + 1)),
        )
        self.assertTrue(
            all(len(row["content_hash"]) == 64 for row in self.category_rows)
        )
        self.assertTrue(all("path" not in row for row in self.category_rows))

    def test_persisted_rows_round_trip_to_validated_artifact(self) -> None:
        stored = validate_persisted_taxonomy(self.version_row, self.category_rows)
        self.assertEqual(stored, self.artifact)

    def test_json_strings_from_legacy_connection_are_supported(self) -> None:
        version = copy.deepcopy(self.version_row)
        import json

        version["artifact_json"] = json.dumps(
            version["artifact_json"], ensure_ascii=False
        )
        rows = copy.deepcopy(self.category_rows)
        for row in rows:
            row["path_json"] = json.dumps(row["path_json"], ensure_ascii=False)
        self.assertEqual(validate_persisted_taxonomy(version, rows), self.artifact)

    def test_version_metadata_mismatch_fails_closed(self) -> None:
        for key, value in (
            ("taxonomy_hash", "0" * 64),
            ("node_count", 999),
            ("leaf_count", 999),
            ("source_row_count", 999),
        ):
            version = copy.deepcopy(self.version_row)
            version[key] = value
            with self.subTest(key=key):
                with self.assertRaisesRegex(TaxonomyPersistenceError, key):
                    validate_persisted_taxonomy(version, self.category_rows)

    def test_missing_reordered_or_modified_category_fails_closed(self) -> None:
        mutations = [
            self.category_rows[:-1],
            list(reversed(self.category_rows)),
            copy.deepcopy(self.category_rows),
        ]
        mutations[-1][0]["name"] = "被篡改"
        for rows in mutations:
            with self.subTest(rows=rows):
                with self.assertRaises(TaxonomyPersistenceError):
                    validate_persisted_taxonomy(self.version_row, rows)

    def test_invalid_timestamp_is_rejected(self) -> None:
        for value in (-1, True, 1.5):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    prepare_taxonomy_rows(self.artifact, created_at=value)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
