from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from idea.object_store import FileObjectStore, ObjectStoreError


class FileObjectStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = FileObjectStore(Path(self.temp.name) / "objects")
        self.content = b"immutable patent content\n"
        self.digest = hashlib.sha256(self.content).hexdigest()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def put(self, key: str = "sha256/fixture.json"):
        return asyncio.run(
            self.store.put_if_absent(
                key,
                self.content,
                expected_sha256=self.digest,
                content_type="application/json",
                encoding="gzip",
            )
        )

    def test_put_is_atomic_private_and_idempotent(self) -> None:
        first = self.put()
        second = self.put()
        self.assertEqual(first.sha256, self.digest)
        self.assertEqual(first, second)
        self.assertEqual(asyncio.run(self.store.get(first.key)), self.content)
        self.assertEqual(asyncio.run(self.store.stat(first.key)), first)
        self.assertEqual(os.stat(Path(self.temp.name) / "objects" / first.key).st_mode & 0o777, 0o600)
        leftovers = list((Path(self.temp.name) / "objects" / "sha256").glob(".object-*"))
        self.assertEqual(leftovers, [])

    def test_existing_key_cannot_be_overwritten_with_different_content(self) -> None:
        self.put()
        other = b"different content"
        with self.assertRaises(ObjectStoreError):
            asyncio.run(
                self.store.put_if_absent(
                    "sha256/fixture.json",
                    other,
                    expected_sha256=hashlib.sha256(other).hexdigest(),
                    content_type="application/json",
                    encoding="gzip",
                )
            )

    def test_wrong_expected_hash_is_rejected_before_writing(self) -> None:
        with self.assertRaises(ObjectStoreError):
            asyncio.run(
                self.store.put_if_absent(
                    "sha256/new.json",
                    self.content,
                    expected_sha256="0" * 64,
                    content_type="application/json",
                    encoding="identity",
                )
            )
        self.assertIsNone(asyncio.run(self.store.stat("sha256/new.json")))

    def test_path_traversal_and_symlinks_are_rejected(self) -> None:
        with self.assertRaises(ObjectStoreError):
            self.put("../outside")
        with self.assertRaises(ObjectStoreError):
            self.put("/absolute")
        outside = Path(self.temp.name) / "outside"
        outside.write_bytes(b"not an object")
        link = Path(self.temp.name) / "objects" / "link"
        link.symlink_to(outside)
        with self.assertRaises(ObjectStoreError):
            asyncio.run(self.store.stat("link"))

    def test_missing_object_is_distinct_from_empty_object(self) -> None:
        self.assertIsNone(asyncio.run(self.store.stat("missing")))
        with self.assertRaises(ObjectStoreError):
            asyncio.run(self.store.get("missing"))


if __name__ == "__main__":
    unittest.main()
