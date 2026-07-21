from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from idea.config import ConfigError, PROJECT_ROOT, load_config


CONFIG_PATH = PROJECT_ROOT / "config" / "ai4patent.json"
SCHEMA_PATH = PROJECT_ROOT / "config" / "ai4patent.schema.json"


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    def write_config(self, raw: dict, directory: str) -> Path:
        path = Path(directory) / "config.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        return path

    def test_default_configuration_is_valid_and_resolves_paths(self) -> None:
        config = load_config(CONFIG_PATH)
        self.assertEqual(config.storage.cache.max_bytes, 1024**3)
        self.assertEqual(config.storage.cache.eviction_policy, "fifo")
        self.assertFalse(config.storage.history.auto_delete)
        self.assertTrue(config.storage.history.manual_delete_enabled)
        self.assertEqual(config.search.mode().deep_review_min, 10)
        self.assertTrue(config.storage.database.is_absolute())
        self.assertEqual(config.model.default, "deepseek-v4-flash")
        self.assertEqual(config.search.providers.exa_mcp.fetch_tool, "web_fetch_exa")
        self.assertEqual(config.search.providers.exa_mcp.fetch_max_characters, 300_000)
        self.assertFalse(config.features.patent_corpus)
        self.assertFalse(config.features.initial_review_rag)
        self.assertFalse(config.features.followup_rag)
        snapshot = config.snapshot()
        self.assertNotIn("api_key", snapshot["model"])
        self.assertNotIn("apiKey", snapshot["model"])

    def test_environment_can_select_config_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_config(self.raw, directory)
            with patch.dict("os.environ", {"AI4PATENT_CONFIG": str(path)}):
                self.assertEqual(load_config().source_path, path.resolve())

    def test_deep_review_minimum_cannot_drop_below_ten(self) -> None:
        self.raw["search"]["modes"]["quick"]["deep_review_min"] = 9
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ConfigError):
                load_config(self.write_config(self.raw, directory))

    def test_cache_must_be_fifo_and_low_watermark_must_fit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bad_policy = json.loads(json.dumps(self.raw))
            bad_policy["storage"]["cache"]["eviction_policy"] = "lru"
            with self.assertRaises(ConfigError):
                load_config(self.write_config(bad_policy, directory))

            bad_watermark = json.loads(json.dumps(self.raw))
            bad_watermark["storage"]["cache"]["low_watermark_bytes"] = 2**40
            with self.assertRaises(ConfigError):
                load_config(self.write_config(bad_watermark, directory))

    def test_unfinished_features_cannot_be_enabled(self) -> None:
        self.raw["features"]["pct"] = True
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ConfigError):
                load_config(self.write_config(self.raw, directory))

    def test_rag_feature_gates_follow_dependency_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            initial_without_corpus = json.loads(json.dumps(self.raw))
            initial_without_corpus["features"]["initial_review_rag"] = True
            with self.assertRaises(ConfigError):
                load_config(self.write_config(initial_without_corpus, directory))

            followup_without_initial = json.loads(json.dumps(self.raw))
            followup_without_initial["features"]["patent_corpus"] = True
            followup_without_initial["features"]["followup_rag"] = True
            with self.assertRaises(ConfigError):
                load_config(self.write_config(followup_without_initial, directory))

            enabled = json.loads(json.dumps(self.raw))
            enabled["features"]["patent_corpus"] = True
            enabled["features"]["initial_review_rag"] = True
            enabled["features"]["followup_rag"] = True
            config = load_config(self.write_config(enabled, directory))
            self.assertTrue(config.features.followup_rag)

    def test_json_schema_declares_rag_feature_gates(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        features = schema["properties"]["features"]
        self.assertTrue(
            {"patent_corpus", "initial_review_rag", "followup_rag"}.issubset(
                features["required"]
            )
        )
        encoded_conditions = json.dumps(features["allOf"], sort_keys=True)
        self.assertIn("patent_corpus", encoded_conditions)
        self.assertIn("initial_review_rag", encoded_conditions)
        self.assertIn("followup_rag", encoded_conditions)

    def test_unknown_settings_fail_closed(self) -> None:
        self.raw["surprise"] = True
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ConfigError):
                load_config(self.write_config(self.raw, directory))


if __name__ == "__main__":
    unittest.main()
