from __future__ import annotations

import unittest
from pathlib import Path

from landscape.classification_terminal import ClassificationAction, ClassificationResult, ClassificationTerminal, reconcile_terminals, validate_classification
from landscape.taxonomy import compile_taxonomy_file


class LandscapeClassificationTerminalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.taxonomy=compile_taxonomy_file(Path("development/landscape/classify.md"))
        cls.leaf=cls.taxonomy.leaf_category_ids[0]

    def classified(self, unit, **updates):
        values=dict(analysis_unit_id=unit,action=ClassificationAction.EXACT_CATEGORY,terminal=ClassificationTerminal.CLASSIFIED,primary_category_id=self.leaf,evidence_ids=("EV-1",),confidence=.9,review_round=0)
        values.update(updates); return ClassificationResult(**values)

    def test_exact_category_must_be_known_leaf_and_evidence_bound(self):
        unit="AU-0000000000000001"; value=self.classified(unit)
        self.assertEqual(validate_classification(value,expected_analysis_unit_id=unit,allowed_evidence_ids={"EV-1"},taxonomy=self.taxonomy),value)
        with self.assertRaisesRegex(ValueError,"non-leaf"):
            validate_classification(self.classified(unit,primary_category_id="CAT-UNKNOWN"),expected_analysis_unit_id=unit,allowed_evidence_ids={"EV-1"},taxonomy=self.taxonomy)

    def test_500_units_reconcile_to_exact_three_way_partition(self):
        ids=tuple(f"AU-{index:016x}" for index in range(500))
        results=[]
        for index, unit in enumerate(ids):
            if index % 10 == 8:
                results.append(ClassificationResult(analysis_unit_id=unit,action=ClassificationAction.NONE_OF_CANDIDATES,terminal=ClassificationTerminal.OTHERS,confidence=.5,review_round=1))
            elif index % 10 == 9:
                results.append(ClassificationResult(analysis_unit_id=unit,action=ClassificationAction.UNRESOLVED,terminal=ClassificationTerminal.UNRESOLVED,confidence=0,review_round=0,unresolved_reason="ABSTRACT_INSUFFICIENT"))
            else: results.append(self.classified(unit))
        partition=reconcile_terminals(ids,tuple(results))
        self.assertEqual(sum(len(values) for values in partition.values()),500)
        self.assertEqual(len(partition[ClassificationTerminal.OTHERS]),50)
        self.assertEqual(len(partition[ClassificationTerminal.UNRESOLVED]),50)

    def test_missing_duplicate_or_extra_result_fails_closed(self):
        ids=("AU-0000000000000001","AU-0000000000000002")
        with self.assertRaisesRegex(ValueError,"mismatch"):
            reconcile_terminals(ids,(self.classified(ids[0]),))
        with self.assertRaisesRegex(ValueError,"duplicates"):
            reconcile_terminals(ids,(self.classified(ids[0]),self.classified(ids[0])))


if __name__ == "__main__": unittest.main()
