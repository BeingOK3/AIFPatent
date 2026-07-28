from __future__ import annotations

import unittest
from collections import Counter
from collections.abc import Mapping
from datetime import date

from tests.landscape_agent_fixture_loader import (
    FIXTURE_ROOT,
    iter_fixture_files,
    load_json,
    load_jsonl,
)


class LandscapeAgentFixtureContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.expected = load_json("expected/base-01.json")
        self.hits = load_jsonl("provider_hits.jsonl")
        self.documents = load_jsonl("fetched_documents.jsonl")
        self.aliases = load_json("alias_registry.json")
        self.analyses = load_jsonl("patent_analysis_outputs.jsonl")
        self.profiles = load_jsonl("company_profile_outputs.jsonl")

    def test_fixture_inventory_is_complete_and_parseable(self) -> None:
        expected_paths = {
            "alias_registry.json",
            "company_profile_outputs.jsonl",
            "expected/base-01.json",
            "fetched_documents.jsonl",
            "graph_cases.json",
            "patent_analysis_outputs.jsonl",
            "provider_hits.jsonl",
            "scopes.json",
            "trend_outputs.jsonl",
        }
        paths = {
            path.relative_to(FIXTURE_ROOT).as_posix()
            for path in iter_fixture_files()
        }
        self.assertEqual(paths, expected_paths)

        for path in iter_fixture_files():
            relative = path.relative_to(FIXTURE_ROOT).as_posix()
            if path.suffix == ".json":
                self.assertIsInstance(load_json(relative), Mapping)
            else:
                self.assertTrue(load_jsonl(relative))

    def test_loader_returns_deeply_read_only_values_and_blocks_escape(self) -> None:
        with self.assertRaises(TypeError):
            self.expected["coverage"]["raw_hit_count"] = 0
        with self.assertRaises(AttributeError):
            self.hits[0]["raw"].update({"changed": True})
        with self.assertRaises(ValueError):
            load_json("../outside.json")

    def test_base_01_scope_and_raw_hit_contract(self) -> None:
        scope = load_json("scopes.json")["cases"]["BASE-01"]
        self.assertEqual(scope["technology_direction"], "数据中心液冷")
        self.assertEqual(
            (date.fromisoformat(scope["publication_start"]), date.fromisoformat(scope["publication_end"])),
            (date(2026, 4, 1), date(2026, 6, 30)),
        )
        self.assertEqual(len(self.hits), self.expected["coverage"]["raw_hit_count"])
        self.assertEqual({hit["case_id"] for hit in self.hits}, {"BASE-01"})
        self.assertEqual(len({hit["hit_id"] for hit in self.hits}), len(self.hits))

        excluded = self.expected["excluded_hits"]
        eligible = [hit for hit in self.hits if hit["hit_id"] not in excluded]
        self.assertEqual(len(eligible), self.expected["coverage"]["eligible_hit_count"])
        normalized = [
            hit["publication_number"].replace("-", "")
            for hit in eligible
        ]
        self.assertEqual(len(set(normalized)), self.expected["coverage"]["unique_eligible_count"])
        self.assertEqual(len(normalized) - len(set(normalized)), 1)

    def test_documents_assignments_and_analysis_cover_exactly_u(self) -> None:
        publications = set(self.expected["eligible_publication_numbers"])
        self.assertEqual(
            {document["publication_number"] for document in self.documents},
            publications,
        )
        assignments = self.aliases["assignments"]
        self.assertEqual(
            {assignment["publication_number"] for assignment in assignments},
            publications,
        )
        self.assertEqual(
            {analysis["publication_number"] for analysis in self.analyses},
            publications,
        )
        self.assertTrue(all(analysis["agent_name"] == "patent_analysis" for analysis in self.analyses))

        actual_counts = Counter(
            assignment["primary_company_id"] for assignment in assignments
        )
        self.assertEqual(dict(actual_counts), dict(self.expected["company_patent_counts"]))
        self.assertEqual(
            next(
                assignment
                for assignment in assignments
                if assignment["publication_number"] == "US20260100002A1"
            )["primary_company_id"],
            "CO-META",
        )
        self.assertEqual(
            next(
                assignment
                for assignment in assignments
                if assignment["publication_number"] == "EP4600001A1"
            )["primary_company_id"],
            "CO-METALLURGY",
        )

    def test_company_profiles_partition_patents_and_only_reference_valid_evidence(self) -> None:
        publications = set(self.expected["eligible_publication_numbers"])
        evidence_by_publication = {
            analysis["publication_number"]: {
                evidence["evidence_id"]
                for evidence in analysis["output"]["evidence"]
            }
            for analysis in self.analyses
        }
        profile_members: list[str] = []
        for profile in self.profiles:
            allowed = set(profile["input_publication_numbers"])
            category_members: list[str] = []
            for category in profile["output"]["technology_categories"]:
                members = set(category["publication_numbers"])
                self.assertLessEqual(members, allowed)
                category_members.extend(category["publication_numbers"])
                valid_evidence = set().union(
                    *(evidence_by_publication[publication] for publication in members)
                )
                self.assertLessEqual(set(category["evidence_ids"]), valid_evidence)
            self.assertCountEqual(category_members, allowed)
            profile_members.extend(category_members)

        self.assertEqual(set(profile_members), publications)
        self.assertEqual(len(profile_members), len(set(profile_members)))

    def test_single_time_bucket_does_not_claim_directional_trend(self) -> None:
        trend = load_jsonl("trend_outputs.jsonl")[0]
        self.assertEqual(tuple(trend["program_statistics"]["buckets"]), ("2026-Q2",))
        self.assertEqual(trend["output"]["trends"], ())
        limitations = " ".join(trend["output"]["limitations"]).lower()
        for forbidden in ("growth", "decline", "acceleration", "shift"):
            self.assertIn(forbidden, limitations)

    def test_graph_case_fan_out_matches_company_buckets(self) -> None:
        graph_case = load_json("graph_cases.json")["cases"][0]
        self.assertEqual(graph_case["case_id"], "BASE-01")
        self.assertEqual(
            set(graph_case["expected_company_sends"]),
            set(self.expected["company_patent_counts"]),
        )
        self.assertEqual(graph_case["expected_audit_decision"], "PASS")


if __name__ == "__main__":
    unittest.main()
