from __future__ import annotations

import unittest

from landscape.classification_terminal import (
    ClassificationAction,
    ClassificationResult,
    ClassificationTerminal,
)
from landscape.direction_record import DirectionRecord, DirectionStatus
from landscape.others_discovery import (
    OthersClusterKind,
    OthersNamingProposal,
    _complete_link_partition,
    apply_others_naming,
    discover_others_directions,
)


def direction(index: int, text: str, keywords: tuple[str, ...]) -> DirectionRecord:
    return DirectionRecord(
        analysis_unit_id=f"AU-{index:016x}",
        status=DirectionStatus.AVAILABLE,
        evidence_sufficient=True,
        technical_problem=text,
        solution_mechanism=text,
        technical_object=text,
        direction_summary=text,
        keywords=keywords,
        evidence_ids=(f"EV-{index}",),
        confidence=0.8,
    )


def classification(index: int, terminal=ClassificationTerminal.OTHERS):
    if terminal == ClassificationTerminal.OTHERS:
        return ClassificationResult(
            analysis_unit_id=f"AU-{index:016x}",
            action=ClassificationAction.NONE_OF_CANDIDATES,
            terminal=terminal,
            confidence=0.8,
            review_round=1,
        )
    return ClassificationResult(
        analysis_unit_id=f"AU-{index:016x}",
        action=ClassificationAction.EXACT_CATEGORY,
        terminal=terminal,
        primary_category_id="C-1",
        confidence=0.8,
        review_round=0,
    )


class LandscapeOthersDiscoveryTests(unittest.TestCase):
    def test_complete_link_prevents_weak_edge_chaining(self):
        similarities = {
            ("A", "B"): 0.8,
            ("A", "C"): 0.2,
            ("B", "C"): 0.8,
        }
        result = _complete_link_partition(("A", "B", "C"), similarities, 0.7)
        self.assertEqual(result, (("A", "B"), ("C",)))

    def test_only_others_are_clustered_as_exact_stable_partition(self):
        records = (
            direction(1, "毫米波 雷达 目标 检测 信号 处理", ("毫米波", "雷达")),
            direction(2, "毫米波 雷达 目标 跟踪 信号 处理", ("毫米波", "雷达")),
            direction(3, "植物 蛋白 基因 编辑 培育", ("基因编辑",)),
            direction(4, "不应进入", ("分类内",)),
        )
        classifications = (
            classification(3),
            classification(1),
            classification(4, ClassificationTerminal.CLASSIFIED),
            classification(2),
        )
        first = discover_others_directions(classifications, records, merge_threshold=0.3)
        second = discover_others_directions(
            tuple(reversed(classifications)),
            tuple(reversed(records)),
            merge_threshold=0.3,
        )
        self.assertEqual(first, second)
        self.assertEqual(
            first.source_member_ids,
            ("AU-0000000000000001", "AU-0000000000000002", "AU-0000000000000003"),
        )
        self.assertEqual(sorted(len(cluster.member_ids) for cluster in first.clusters), [1, 2])
        by_size = {len(cluster.member_ids): cluster for cluster in first.clusters}
        self.assertEqual(by_size[2].kind, OthersClusterKind.CANDIDATE)
        self.assertEqual(by_size[1].kind, OthersClusterKind.SINGLETON)
        self.assertNotIn("AU-0000000000000004", first.source_member_ids)

    def test_low_information_singleton_is_explicit_noise(self):
        result = discover_others_directions(
            (classification(1),),
            (direction(1, "x", ()),),
        )
        self.assertEqual(result.clusters[0].kind, OthersClusterKind.NOISE)

    def test_model_can_name_but_cannot_change_members(self):
        discovery = discover_others_directions(
            (classification(1),),
            (direction(1, "毫米波 雷达 目标 检测", ("雷达",)),),
        )
        cluster = discovery.clusters[0]
        proposal = OthersNamingProposal(
            cluster_id=cluster.cluster_id,
            member_ids=cluster.member_ids,
            name="毫米波目标感知",
            common_mechanism="回波检测",
            keywords=("毫米波", "目标感知"),
        )
        named = apply_others_naming(cluster, proposal)
        self.assertEqual(named.member_ids, cluster.member_ids)
        self.assertEqual(named.naming_source, "MODEL")
        with self.assertRaisesRegex(ValueError, "cannot change"):
            apply_others_naming(
                cluster,
                proposal.model_copy(update={"member_ids": ("AU-ffffffffffffffff",)}),
            )

    def test_missing_or_unavailable_direction_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "missing"):
            discover_others_directions((classification(1),), ())
        unavailable = DirectionRecord(
            analysis_unit_id="AU-0000000000000001",
            status=DirectionStatus.UNRESOLVED,
            evidence_sufficient=False,
            unresolved_reason="abstract missing",
            confidence=0,
        )
        with self.assertRaisesRegex(ValueError, "available"):
            discover_others_directions((classification(1),), (unavailable,))


if __name__ == "__main__":
    unittest.main()
