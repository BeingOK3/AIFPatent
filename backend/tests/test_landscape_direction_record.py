from __future__ import annotations

import unittest
from pathlib import Path

from landscape.abstract_evidence import abstract_provider_failure
from landscape.direction_record import DirectionRecord, DirectionStatus, unresolved_from_abstract, validate_direction_record
from landscape.taxonomy import compile_taxonomy_file


class LandscapeDirectionRecordTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.taxonomy=compile_taxonomy_file(Path("development/landscape/classify.md"))
        cls.parent=next(node.category_id for node in cls.taxonomy.nodes if node.level==1)
        cls.unit="AU-0000000000000001"

    def record(self, **updates):
        values=dict(analysis_unit_id=self.unit,status=DirectionStatus.AVAILABLE,evidence_sufficient=True,technical_problem="降低通信误差",solution_mechanism="根据导频估计信道并恢复数据",technical_object="无线信号",direction_summary="利用导频信号进行信道估计和数据恢复",keywords=("导频","信道估计"),candidate_level1_ids=(self.parent,),confidence=.9,evidence_ids=("EV-PUB-1-A01",))
        values.update(updates); return DirectionRecord(**values)

    def test_valid_record_is_unit_evidence_and_taxonomy_bound(self):
        value=self.record()
        self.assertEqual(validate_direction_record(value,expected_analysis_unit_id=self.unit,allowed_evidence_ids={"EV-PUB-1-A01"},taxonomy=self.taxonomy),value)

    def test_unknown_unit_evidence_or_parent_fails_closed(self):
        with self.assertRaisesRegex(ValueError,"another analysis unit"):
            validate_direction_record(self.record(),expected_analysis_unit_id="AU-0000000000000002",allowed_evidence_ids={"EV-PUB-1-A01"},taxonomy=self.taxonomy)
        with self.assertRaisesRegex(ValueError,"unknown evidence"):
            validate_direction_record(self.record(),expected_analysis_unit_id=self.unit,allowed_evidence_ids=set(),taxonomy=self.taxonomy)
        with self.assertRaisesRegex(ValueError,"unknown taxonomy"):
            validate_direction_record(self.record(candidate_level1_ids=("CAT-UNKNOWN",)),expected_analysis_unit_id=self.unit,allowed_evidence_ids={"EV-PUB-1-A01"},taxonomy=self.taxonomy)

    def test_unavailable_abstract_short_circuits_to_unresolved(self):
        value=unresolved_from_abstract(self.unit,abstract_provider_failure("PUB-1","fixture","TIMEOUT"))
        self.assertEqual(value.status,DirectionStatus.UNRESOLVED)
        self.assertEqual(value.unresolved_reason,"TIMEOUT")


if __name__ == "__main__": unittest.main()
