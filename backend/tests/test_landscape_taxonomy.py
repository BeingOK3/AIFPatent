from __future__ import annotations

import json
import unittest
from pathlib import Path

from landscape.taxonomy import (
    TaxonomyArtifact,
    TaxonomyCompileError,
    compile_taxonomy_file,
    compile_taxonomy_markdown,
)


ROOT = Path(__file__).resolve().parents[2]
CLASSIFY_PATH = ROOT / "development" / "landscape" / "classify.md"


def markdown(*rows: str) -> str:
    return "\n".join(
        [
            "taxonomy",
            "",
            "| 一级分类 | 二级分类 | 三级分类 |",
            "| --- | --- | --- |",
            *rows,
        ]
    )


class LandscapeTaxonomyCompilerTests(unittest.TestCase):
    def test_current_classification_source_compiles_without_silent_loss(self) -> None:
        artifact = compile_taxonomy_file(CLASSIFY_PATH)
        self.assertEqual(artifact.source_row_count, 632)
        self.assertEqual(len(artifact.leaf_category_ids), 632)
        self.assertEqual(len(artifact.leaf_category_ids), len(set(artifact.leaf_category_ids)))
        self.assertTrue(all(node.category_id.startswith("CAT-") for node in artifact.nodes))
        self.assertTrue(artifact.taxonomy_version.startswith("TAX-"))

    def test_empty_third_level_makes_second_level_the_leaf(self) -> None:
        artifact = compile_taxonomy_markdown(markdown("| 无线通信 | 空口与物理层 |  |"))
        self.assertEqual(len(artifact.nodes), 2)
        leaf = next(node for node in artifact.nodes if node.is_leaf)
        self.assertEqual(leaf.level, 2)
        self.assertEqual(leaf.path, ("无线通信", "空口与物理层"))
        self.assertEqual(artifact.leaf_category_ids, (leaf.category_id,))

    def test_third_level_is_the_deepest_leaf(self) -> None:
        artifact = compile_taxonomy_markdown(
            markdown("| 存储介质 | 固态盘(SSD) | 闪存/NAND存储介质 |")
        )
        leaf = next(node for node in artifact.nodes if node.is_leaf)
        self.assertEqual(leaf.level, 3)
        self.assertEqual(
            leaf.path, ("存储介质", "固态盘(SSD)", "闪存/NAND存储介质")
        )

    def test_same_second_level_under_different_parents_has_distinct_ids(self) -> None:
        artifact = compile_taxonomy_markdown(
            markdown("| 网络 | 负载均衡 |  |", "| 云资源 | 负载均衡 |  |")
        )
        leaf_ids = [node.category_id for node in artifact.nodes if node.is_leaf]
        self.assertEqual(len(leaf_ids), 2)
        self.assertEqual(len(set(leaf_ids)), 2)

    def test_unicode_and_whitespace_are_normalized_before_identity(self) -> None:
        compact = compile_taxonomy_markdown(markdown("| AI计算硬件 | NPU |  |"))
        spaced = compile_taxonomy_markdown(markdown("|  AI计算硬件  |  ＮＰＵ |  |"))
        self.assertEqual(compact.taxonomy_hash, spaced.taxonomy_hash)
        self.assertEqual(compact.leaf_category_ids, spaced.leaf_category_ids)
        self.assertNotEqual(compact.source_hash, spaced.source_hash)

    def test_duplicate_full_path_fails_closed(self) -> None:
        source = markdown("| 网络 | 路由 |  |", "| 网络 | 路由 |  |")
        with self.assertRaisesRegex(TaxonomyCompileError, "duplicate taxonomy path"):
            compile_taxonomy_markdown(source)

    def test_path_cannot_be_both_leaf_and_parent(self) -> None:
        source = markdown("| 网络 | 路由 |  |", "| 网络 | 路由 | SRv6 |")
        with self.assertRaisesRegex(TaxonomyCompileError, "both leaf and parent"):
            compile_taxonomy_markdown(source)

    def test_empty_required_level_fails_closed(self) -> None:
        for row, message in (
            ("|  | 路由 |  |", "empty level 1"),
            ("| 网络 |  |  |", "empty level 2"),
        ):
            with self.subTest(row=row):
                with self.assertRaisesRegex(TaxonomyCompileError, message):
                    compile_taxonomy_markdown(markdown(row))

    def test_invalid_header_alignment_column_or_trailing_text_fails_closed(self) -> None:
        invalid_sources = (
            "| A | B | C |\n| --- | --- | --- |\n| a | b | c |",
            "| 一级分类 | 二级分类 | 三级分类 |\n| -- | --- | --- |\n| a | b | c |",
            markdown("| a | b | c | extra |"),
            markdown("| a | b | c |") + "\nignored",
        )
        for source in invalid_sources:
            with self.subTest(source=source):
                with self.assertRaises(TaxonomyCompileError):
                    compile_taxonomy_markdown(source)

    def test_artifact_json_is_stable_and_self_describing(self) -> None:
        source = markdown("| 网络 | 路由 | SRv6 |", "| 网络 | 交换 |  |")
        first = compile_taxonomy_markdown(source)
        second = compile_taxonomy_markdown(source)
        self.assertEqual(first, second)
        self.assertEqual(first.canonical_json(), second.canonical_json())
        payload = json.loads(first.canonical_json())
        self.assertEqual(payload["taxonomy_hash"], first.taxonomy_hash)
        self.assertEqual(payload["source_row_count"], 2)
        self.assertEqual(TaxonomyArtifact.from_dict(payload), first)

    def test_artifact_loader_rejects_hash_parent_and_leaf_tampering(self) -> None:
        artifact = compile_taxonomy_markdown(
            markdown("| 网络 | 路由 | SRv6 |", "| 网络 | 交换 |  |")
        )
        mutations = []
        bad_hash = artifact.to_dict()
        bad_hash["taxonomy_hash"] = "0" * 64
        mutations.append(bad_hash)

        bad_parent = artifact.to_dict()
        bad_parent["nodes"][1]["parent_id"] = None
        mutations.append(bad_parent)

        bad_leaf = artifact.to_dict()
        bad_leaf["leaf_category_ids"][0] = bad_leaf["nodes"][0]["category_id"]
        mutations.append(bad_leaf)

        for payload in mutations:
            with self.subTest(payload=payload):
                with self.assertRaises(TaxonomyCompileError):
                    TaxonomyArtifact.from_dict(payload)


if __name__ == "__main__":
    unittest.main()
