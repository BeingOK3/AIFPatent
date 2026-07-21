from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from idea.redis_limiter import RedisDistributedLimiter, RedisLimiterError


class RedisDistributedLimiterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = AsyncMock()
        self.limiter = RedisDistributedLimiter(self.client, prefix="test:limiter")

    def test_acquire_returns_scoped_lease_when_lua_grants_it(self) -> None:
        self.client.eval.return_value = 1
        lease = asyncio.run(
            self.limiter.acquire(key="patents.google.com", owner="worker-a", lease_seconds=30)
        )
        self.assertIsNotNone(lease)
        assert lease is not None
        self.assertEqual(lease.key, "patents.google.com")
        self.assertEqual(lease.owner, "worker-a")
        self.assertGreater(lease.expires_at, datetime.now(timezone.utc))
        call = self.client.eval.await_args
        self.assertEqual(call.args[2:4], ("test:limiter:lease:patents.google.com", "test:limiter:cooldown:patents.google.com"))
        self.assertNotIn("API", str(call.args))

    def test_busy_key_returns_no_lease_and_release_uses_token(self) -> None:
        self.client.eval.return_value = 0
        self.assertIsNone(
            asyncio.run(
                self.limiter.acquire(key="origin", owner="worker-a", lease_seconds=10)
            )
        )

        from idea.ports import LimiterLease

        lease = LimiterLease(
            key="origin",
            lease_id="lease-1",
            owner="worker-a",
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=10),
        )
        asyncio.run(self.limiter.release(lease))
        self.assertEqual(self.client.eval.await_args.args[2], "test:limiter:lease:origin")
        self.assertEqual(self.client.eval.await_args.args[3], "lease-1:worker-a")

    def test_defer_requires_aware_time_and_preserves_reason_limit(self) -> None:
        with self.assertRaises(RedisLimiterError):
            asyncio.run(
                self.limiter.defer_until(
                    key="origin", available_at=datetime.now(), reason="blocked"
                )
            )
        when = datetime.now(timezone.utc) + timedelta(minutes=5)
        asyncio.run(self.limiter.defer_until(key="origin", available_at=when, reason="x" * 900))
        call = self.client.eval.await_args
        self.assertEqual(call.args[2], "test:limiter:cooldown:origin")
        self.assertEqual(len(call.args[3]), 500)

    def test_invalid_lease_parameters_fail_before_redis_call(self) -> None:
        with self.assertRaises(RedisLimiterError):
            asyncio.run(self.limiter.acquire(key="", owner="worker", lease_seconds=1))
        with self.assertRaises(RedisLimiterError):
            asyncio.run(self.limiter.acquire(key="origin", owner="", lease_seconds=1))
        with self.assertRaises(RedisLimiterError):
            asyncio.run(self.limiter.acquire(key="origin", owner="worker", lease_seconds=0))
        self.client.eval.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
