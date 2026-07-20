from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from idea.cache import CacheError, CacheStore
from idea.database import Database


class CacheStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.db = Database(root / "idea.db")
        self.db.initialize()
        self.runs_dir = root / "idea-runs"
        self.runs_dir.mkdir()
        self.history_file = self.runs_dir / "must-survive.txt"
        self.history_file.write_text("durable", encoding="utf-8")
        self.cache = CacheStore(
            root / "cache",
            self.db,
            max_bytes=10,
            low_watermark_bytes=6,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_fifo_evicts_oldest_until_low_watermark(self) -> None:
        self.cache.put_bytes("a", "searches", b"aaaa")
        self.cache.put_bytes("b", "searches", b"bbbb")
        self.cache.put_bytes("c", "searches", b"cccc")
        entries = self.cache.entries()
        self.assertEqual([item["cache_key"] for item in entries], ["c"])
        self.assertEqual(self.cache.stats()["size_bytes"], 4)
        self.assertTrue(self.history_file.is_file())

    def test_access_does_not_change_fifo_sequence(self) -> None:
        self.cache.put_bytes("a", "documents", b"aaa")
        self.cache.put_bytes("b", "documents", b"bbb")
        before = {row["cache_key"]: row["sequence"] for row in self.cache.entries()}
        with self.cache.lease("a") as path:
            self.assertEqual(path.read_bytes(), b"aaa")
        after = {row["cache_key"]: row["sequence"] for row in self.cache.entries()}
        self.assertEqual(before, after)

    def test_lease_protects_entry_during_cleanup(self) -> None:
        self.cache.put_bytes("a", "documents", b"aaaa")
        self.cache.put_bytes("b", "documents", b"bbbb")
        with self.cache.lease("a"):
            self.cache.put_bytes("c", "documents", b"cccc")
            self.assertEqual([row["cache_key"] for row in self.cache.entries()], ["a"])
        self.assertEqual(self.cache.stats()["leased_count"], 0)

    def test_oversized_object_is_not_cached(self) -> None:
        path = self.cache.put_bytes("large", "documents", b"x" * 11)
        self.assertIsNone(path)
        self.assertEqual(self.cache.stats()["entry_count"], 0)

    def test_same_key_with_different_content_is_rejected(self) -> None:
        self.cache.put_bytes("stable", "parsed", b"first")
        with self.assertRaisesRegex(CacheError, "collision"):
            self.cache.put_bytes("stable", "parsed", b"second")

    def test_repair_removes_missing_records_and_orphan_files(self) -> None:
        path = self.cache.put_bytes("missing", "searches", b"one")
        assert path is not None
        path.unlink()
        orphan = self.cache.root / "documents" / "orphan"
        orphan.parent.mkdir(parents=True, exist_ok=True)
        orphan.write_bytes(b"orphan")
        result = self.cache.repair()
        self.assertEqual(result, {"missing_records": 1, "orphan_files": 1})
        self.assertEqual(self.cache.stats()["entry_count"], 0)

    def test_invalid_category_is_rejected(self) -> None:
        with self.assertRaises(CacheError):
            self.cache.put_bytes("key", "../runs", b"x")


if __name__ == "__main__":
    unittest.main()
