from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from idea.config import ConfigError, PROJECT_ROOT, load_config


CONFIG_PATH = PROJECT_ROOT / "config" / "ai4patent.json"


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

    def test_unknown_settings_fail_closed(self) -> None:
        self.raw["surprise"] = True
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ConfigError):
                load_config(self.write_config(self.raw, directory))


if __name__ == "__main__":
    unittest.main()
