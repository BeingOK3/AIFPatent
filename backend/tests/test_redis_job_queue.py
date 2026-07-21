from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from idea.ports import JobRequest
from idea.redis_job_queue import RedisJobQueue, RedisJobQueueError


class RedisJobQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = AsyncMock()
        self.queue = RedisJobQueue(self.client, prefix="test:jobs")

    def test_enqueue_serializes_idempotency_and_returns_redis_job_id(self) -> None:
        self.client.eval.return_value = "job-1"
        request = JobRequest(
            kind="corpus.ingest",
            idempotency_key="version-1",
            payload={"version_id": "version-1"},
        )
        self.assertEqual(asyncio.run(self.queue.enqueue(request)), "job-1")
        call = self.client.eval.await_args
        self.assertEqual(call.args[1], 3)
        self.assertIn("test:jobs:pending", call.args)
        self.assertIn("test:jobs:idempotency:version-1", call.args)
        self.assertNotIn("api_key", str(call.args).lower())

    def test_claim_reconstructs_lease_and_request(self) -> None:
        self.client.eval.return_value = "job-1"
        self.client.hgetall.return_value = {
            "lease_id": "lease-1",
            "worker_id": "worker-a",
            "lease_expires_at": str(int(datetime.now(timezone.utc).timestamp()) + 30),
            "request_json": '{"idempotency_key":"v1","kind":"embed","payload":{"chunk_id":"c1"}}',
        }
        lease = asyncio.run(self.queue.claim(worker_id="worker-a", lease_seconds=30))
        self.assertIsNotNone(lease)
        assert lease is not None
        self.assertEqual(lease.job_id, "job-1")
        self.assertEqual(lease.request.payload["chunk_id"], "c1")
        self.assertIsInstance(lease.expires_at, datetime)

    def test_complete_fail_and_cancel_use_atomic_scripts(self) -> None:
        from idea.ports import JobLease

        lease = JobLease(
            job_id="job-1",
            lease_id="lease-1",
            worker_id="worker-a",
            expires_at=datetime.now(timezone.utc),
            request=JobRequest("embed", "v1", {"chunk_id": "c1"}),
        )
        self.client.eval.return_value = 1
        asyncio.run(self.queue.complete(lease, {"status": "ok"}))
        asyncio.run(self.queue.fail(lease, error_code="TEMPORARY", retryable=True))
        self.assertTrue(asyncio.run(self.queue.cancel("job-1")))
        self.assertEqual(self.client.eval.await_count, 3)

    def test_credentials_and_invalid_claim_parameters_are_rejected(self) -> None:
        with self.assertRaises(RedisJobQueueError):
            asyncio.run(
                self.queue.enqueue(JobRequest("run", "id-1", {"api_key": "never"}))
            )
        with self.assertRaises(RedisJobQueueError):
            asyncio.run(self.queue.claim(worker_id="", lease_seconds=10))
        with self.assertRaises(RedisJobQueueError):
            asyncio.run(self.queue.claim(worker_id="worker", lease_seconds=0))
        with self.assertRaises(RedisJobQueueError):
            asyncio.run(self.queue.cancel(""))


if __name__ == "__main__":
    unittest.main()
