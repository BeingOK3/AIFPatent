from __future__ import annotations

import unittest
from pathlib import Path

from landscape.direction_record import DirectionRecord,DirectionStatus
from landscape.taxonomy import compile_taxonomy_file
from landscape.taxonomy_recall import recall_taxonomy_candidates


class LandscapeTaxonomyRecallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.taxonomy=compile_taxonomy_file(Path("development/landscape/classify.md"))
    def test_recall_is_stable_leaf_only_and_parent_bounded(self):
        parent=next(node for node in self.taxonomy.nodes if node.level==1)
        direction=DirectionRecord(analysis_unit_id="AU-0000000000000001",status=DirectionStatus.AVAILABLE,evidence_sufficient=True,technical_problem="无线通信信道误差",solution_mechanism="使用导频进行信道估计",technical_object="通信信号",direction_summary="无线信号处理",keywords=("信道估计",),candidate_level1_ids=(parent.category_id,),confidence=.8,evidence_ids=("EV-1",))
        first=recall_taxonomy_candidates(direction,self.taxonomy,top_k=10)
        second=recall_taxonomy_candidates(direction,self.taxonomy,top_k=10)
        self.assertEqual(first,second)
        self.assertTrue(all(item.category_id in self.taxonomy.leaf_category_ids for item in first))
        self.assertTrue(all(item.source=="LEXICAL_HIERARCHICAL" for item in first))

    def test_top_k_is_hard_bounded(self):
        direction=DirectionRecord(analysis_unit_id="AU-0000000000000001",status=DirectionStatus.AVAILABLE,evidence_sufficient=True,solution_mechanism="数据处理机制",direction_summary="数据处理",confidence=.5,evidence_ids=("EV-1",))
        self.assertLessEqual(len(recall_taxonomy_candidates(direction,self.taxonomy,top_k=3)),3)


if __name__ == "__main__": unittest.main()
