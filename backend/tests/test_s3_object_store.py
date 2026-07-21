from __future__ import annotations

import asyncio
import hashlib
import unittest

from idea.object_store import ObjectStoreError
from idea.s3_object_store import S3ObjectStore


class FakeBody:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def read(self) -> bytes:
        return self.content


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[str, dict] = {}

    def head_bucket(self, *, Bucket: str):
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def head_object(self, *, Bucket: str, Key: str):
        if Key not in self.objects:
            error = RuntimeError("not found")
            error.response = {"Error": {"Code": "404"}}
            raise error
        item = self.objects[Key]
        return {
            "ContentLength": len(item["body"]),
            "ContentType": item["content_type"],
            "Metadata": item["metadata"],
        }

    def put_object(self, **request):
        if request["Key"] in self.objects:
            error = RuntimeError("precondition")
            error.response = {"Error": {"Code": "PreconditionFailed"}}
            raise error
        self.objects[request["Key"]] = {
            "body": request["Body"],
            "content_type": request["ContentType"],
            "metadata": request["Metadata"],
        }

    def get_object(self, *, Bucket: str, Key: str):
        item = self.objects[Key]
        return {"Body": FakeBody(item["body"]), "Metadata": item["metadata"]}


class S3ObjectStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = FakeS3Client()
        self.store = S3ObjectStore(
            bucket="aifpatent",
            endpoint_url="http://127.0.0.1:9000",
            access_key="access",
            secret_key="secret",
            client=self.client,
            run_in_thread=False,
        )
        self.content = b"immutable patent content"
        self.digest = hashlib.sha256(self.content).hexdigest()

    def put(self, key: str = "corpus/fixture.json"):
        return asyncio.run(
            self.store.put_if_absent(
                key,
                self.content,
                expected_sha256=self.digest,
                content_type="application/json",
                encoding="identity",
            )
        )

    def test_put_get_stat_is_immutable_and_idempotent(self) -> None:
        first = self.put()
        second = self.put()
        self.assertEqual(first, second)
        self.assertEqual(asyncio.run(self.store.get(first.key)), self.content)
        self.assertEqual(asyncio.run(self.store.stat(first.key)), first)

    def test_conflicting_key_and_hash_are_rejected(self) -> None:
        self.put()
        other = b"different"
        with self.assertRaises(ObjectStoreError):
            asyncio.run(
                self.store.put_if_absent(
                    "corpus/fixture.json",
                    other,
                    expected_sha256=hashlib.sha256(other).hexdigest(),
                    content_type="application/json",
                    encoding="identity",
                )
            )
        with self.assertRaises(ObjectStoreError):
            asyncio.run(
                self.store.put_if_absent(
                    "corpus/new.json",
                    self.content,
                    expected_sha256="0" * 64,
                    content_type="application/json",
                    encoding="identity",
                )
            )

    def test_invalid_keys_and_unverified_objects_fail_closed(self) -> None:
        with self.assertRaises(ObjectStoreError):
            self.put("../outside")
        self.client.objects["corpus/bad.json"] = {
            "body": b"bad",
            "content_type": "application/json",
            "metadata": {},
        }
        with self.assertRaises(ObjectStoreError):
            asyncio.run(self.store.stat("corpus/bad.json"))


if __name__ == "__main__":
    unittest.main()
