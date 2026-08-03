from __future__ import annotations

import json
import time
import tracemalloc
import unittest

from backend.tests.landscape_v4_fixture_factory import (
    INPUT_MODES,
    SCALE_CASE_SIZES,
    make_scale_case,
)


class LandscapeV4ScaleFixtureTests(unittest.TestCase):
    def test_land_050_covers_all_three_input_modes(self) -> None:
        for mode in INPUT_MODES:
            with self.subTest(mode=mode):
                case = make_scale_case("LAND-050", mode=mode)
                scope = case["scope"]
                self.assertEqual(case["mode"], mode)
                self.assertEqual(bool(scope["companies"]), mode != "TECHNOLOGY_ONLY")
                self.assertEqual(scope["technology"] is not None, mode != "COMPANY_ONLY")

    def test_scale_case_cardinality_and_shards_are_deterministic(self) -> None:
        for case_name, expected_size in SCALE_CASE_SIZES.items():
            with self.subTest(case_name=case_name):
                first = make_scale_case(case_name)
                second = make_scale_case(case_name)
                self.assertEqual(first, second)
                self.assertEqual(len(first["publications"]), expected_size)
                self.assertEqual(
                    first["expected"]["shard_count"], (expected_size + 999) // 1000
                )

    def test_publication_identity_and_links_are_unique(self) -> None:
        case = make_scale_case("LAND-2500-SHARDED")
        publications = case["publications"]
        numbers = [item["publication_number"] for item in publications]
        identities = [item["publication_id"] for item in publications]
        self.assertEqual(len(numbers), len(set(numbers)))
        self.assertEqual(len(identities), len(set(identities)))
        self.assertTrue(
            all(
                item["source_url"].endswith(item["publication_number"])
                for item in publications
            )
        )

    def test_fixture_is_bilingual_and_json_serializable(self) -> None:
        case = make_scale_case("LAND-500")
        encoded = json.dumps(case, ensure_ascii=False, sort_keys=True)
        self.assertIn("人工智能", encoded)
        self.assertIn("artificial intelligence", encoded)
        self.assertGreater(len(encoded), 100_000)

    def test_2500_fixture_generation_has_a_bounded_baseline(self) -> None:
        tracemalloc.start()
        started = time.perf_counter()
        case = make_scale_case("LAND-2500-SHARDED")
        elapsed = time.perf_counter() - started
        _, peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        self.assertEqual(len(case["publications"]), 2500)
        self.assertLess(elapsed, 5.0)
        self.assertLess(peak_bytes, 32 * 1024 * 1024)

    def test_unknown_case_and_mode_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown Landscape v4 scale case"):
            make_scale_case("LAND-UNKNOWN")
        with self.assertRaisesRegex(ValueError, "unsupported Landscape v4 input mode"):
            make_scale_case("LAND-050", mode="AUTO")


if __name__ == "__main__":
    unittest.main()
