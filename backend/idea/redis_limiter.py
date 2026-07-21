from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from .ports import DistributedLimiter, LimiterLease


class RedisLimiterError(RuntimeError):
    pass


_ACQUIRE_SCRIPT = """
if redis.call('EXISTS', KEYS[2]) == 1 then return 0 end
if redis.call('SET', KEYS[1], ARGV[1], 'NX', 'EX', ARGV[2]) then return 1 end
return 0
"""

_RELEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""

_DEFER_SCRIPT = """
local now = redis.call('TIME')
local now_ms = (tonumber(now[1]) * 1000) + math.floor(tonumber(now[2]) / 1000)
local requested_ms = tonumber(ARGV[2]) - now_ms
if requested_ms < 0 then requested_ms = 0 end
local existing_ms = redis.call('PTTL', KEYS[1])
if existing_ms < requested_ms then
    redis.call('SET', KEYS[1], ARGV[1], 'PX', requested_ms)
end
return 1
"""


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise RedisLimiterError("available_at must be timezone-aware")
    return value.astimezone(timezone.utc)


class RedisDistributedLimiter:
    """Redis-backed atomic lease and cooldown adapter.

    The client is intentionally injected so the domain layer does not import or
    construct a global Redis connection. `redis.asyncio.Redis` from redis-py is
    the production client; tests can provide a small protocol-compatible fake.
    """

    def __init__(self, client: Any, *, prefix: str = "aifpatent:limiter"):
        if not prefix.strip():
            raise RedisLimiterError("prefix must not be empty")
        self.client = client
        self.prefix = prefix.rstrip(":")

    @classmethod
    def from_url(cls, url: str, *, prefix: str = "aifpatent:limiter") -> "RedisDistributedLimiter":
        try:
            import redis.asyncio as redis
        except ImportError as exc:
            raise RedisLimiterError(
                "redis-py is required for RedisDistributedLimiter.from_url"
            ) from exc
        return cls(redis.from_url(url, decode_responses=True), prefix=prefix)

    def _keys(self, key: str) -> tuple[str, str]:
        if not key.strip() or any(character in key for character in "\r\n"):
            raise RedisLimiterError("limiter key must be non-empty and single-line")
        return f"{self.prefix}:lease:{key}", f"{self.prefix}:cooldown:{key}"

    async def acquire(
        self, *, key: str, owner: str, lease_seconds: int
    ) -> LimiterLease | None:
        if not owner.strip() or lease_seconds < 1:
            raise RedisLimiterError("owner is required and lease_seconds must be positive")
        lease_key, cooldown_key = self._keys(key)
        lease_id = secrets.token_urlsafe(24)
        acquired = await self.client.eval(
            _ACQUIRE_SCRIPT,
            2,
            lease_key,
            cooldown_key,
            f"{lease_id}:{owner}",
            lease_seconds,
        )
        if not acquired:
            return None
        expires_at = datetime.now(timezone.utc).replace(microsecond=0)
        expires_at += timedelta(seconds=lease_seconds)
        return LimiterLease(key=key, lease_id=lease_id, owner=owner, expires_at=expires_at)

    async def release(self, lease: LimiterLease) -> None:
        lease_key, _ = self._keys(lease.key)
        await self.client.eval(
            _RELEASE_SCRIPT,
            1,
            lease_key,
            f"{lease.lease_id}:{lease.owner}",
        )

    async def defer_until(self, *, key: str, available_at: datetime, reason: str) -> None:
        available_at = _require_aware(available_at)
        _, cooldown_key = self._keys(key)
        epoch_ms = int(available_at.timestamp() * 1000)
        await self.client.eval(
            _DEFER_SCRIPT,
            1,
            cooldown_key,
            reason[:500] or "cooldown",
            epoch_ms,
        )


__all__ = ["RedisDistributedLimiter", "RedisLimiterError"]
