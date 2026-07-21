from __future__ import annotations

import json
import secrets
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Mapping

from .ports import JobLease, JobQueue, JobRequest


class RedisJobQueueError(RuntimeError):
    pass


_FORBIDDEN_FIELDS = {
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "token",
}


_ENQUEUE_SCRIPT = """
local existing = redis.call('GET', KEYS[2])
if existing then return existing end
redis.call('SET', KEYS[2], ARGV[1])
redis.call('HSET', KEYS[3],
    'status', 'QUEUED',
    'kind', ARGV[2],
    'idempotency_key', ARGV[3],
    'request_json', ARGV[4],
    'attempts', '0')
redis.call('RPUSH', KEYS[1], ARGV[1])
return ARGV[1]
"""

_CLAIM_SCRIPT = """
local job_id = redis.call('RPOPLPUSH', KEYS[1], KEYS[2])
if not job_id then return '' end
local lease_key = ARGV[6] .. job_id
local job_key = ARGV[7] .. job_id
if redis.call('EXISTS', job_key) == 0 then
    redis.call('LREM', KEYS[2], 1, job_id)
    return ''
end
local lease_value = ARGV[1]
if not redis.call('SET', lease_key, lease_value, 'NX', 'EX', ARGV[2]) then
    redis.call('LREM', KEYS[2], 1, job_id)
    redis.call('RPUSH', KEYS[1], job_id)
    return ''
end
redis.call('HSET', job_key,
    'status', 'RUNNING',
    'worker_id', ARGV[3],
    'lease_id', ARGV[4],
    'lease_expires_at', ARGV[5])
return job_id
"""

_COMPLETE_SCRIPT = """
local job_id = ARGV[1]
local expected = ARGV[2]
if redis.call('GET', KEYS[1]) ~= expected then return 0 end
if redis.call('HGET', KEYS[3], 'status') ~= 'RUNNING' then return 0 end
redis.call('HSET', KEYS[3], 'status', 'COMPLETED', 'result_json', ARGV[3])
redis.call('DEL', KEYS[1])
redis.call('LREM', KEYS[2], 1, job_id)
return 1
"""

_FAIL_SCRIPT = """
local job_id = ARGV[1]
local expected = ARGV[2]
if redis.call('GET', KEYS[1]) ~= expected then return 0 end
if redis.call('HGET', KEYS[3], 'status') ~= 'RUNNING' then return 0 end
local attempts = redis.call('HINCRBY', KEYS[3], 'attempts', 1)
redis.call('HSET', KEYS[3], 'error_code', ARGV[3])
redis.call('DEL', KEYS[1])
redis.call('LREM', KEYS[2], 1, job_id)
if ARGV[4] == '1' then
    redis.call('HSET', KEYS[3], 'status', 'QUEUED')
    redis.call('RPUSH', KEYS[4], job_id)
else
    redis.call('HSET', KEYS[3], 'status', 'FAILED')
end
return attempts
"""

_CANCEL_SCRIPT = """
local status = redis.call('HGET', KEYS[3], 'status')
if not status or status == 'COMPLETED' or status == 'FAILED' or status == 'CANCELLED' then return 0 end
redis.call('HSET', KEYS[3], 'status', 'CANCELLED')
redis.call('DEL', KEYS[1])
redis.call('LREM', KEYS[2], 1, ARGV[1])
redis.call('LREM', KEYS[4], 1, ARGV[1])
return 1
"""


def _contains_forbidden_field(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            key.strip().lower() in _FORBIDDEN_FIELDS or _contains_forbidden_field(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_forbidden_field(item) for item in value)
    return False


def _json(value: Any, *, label: str) -> str:
    if _contains_forbidden_field(value):
        raise RedisJobQueueError(f"{label} contains a forbidden credential field")
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise RedisJobQueueError(f"{label} is not JSON serializable") from exc


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


class RedisJobQueue:
    """Redis list/hash job queue with atomic lease transitions."""

    def __init__(self, client: Any, *, prefix: str = "aifpatent:jobs"):
        if not prefix.strip():
            raise RedisJobQueueError("prefix must not be empty")
        self.client = client
        self.prefix = prefix.rstrip(":")

    @classmethod
    def from_url(cls, url: str, *, prefix: str = "aifpatent:jobs") -> "RedisJobQueue":
        try:
            import redis.asyncio as redis
        except ImportError as exc:
            raise RedisJobQueueError("redis-py is required for RedisJobQueue.from_url") from exc
        return cls(redis.from_url(url, decode_responses=True), prefix=prefix)

    def _key(self, suffix: str) -> str:
        return f"{self.prefix}:{suffix}"

    def _job_key(self, job_id: str) -> str:
        return self._key(f"job:{job_id}")

    def _lease_key(self, job_id: str) -> str:
        return self._key(f"lease:{job_id}")

    async def enqueue(self, request: JobRequest) -> str:
        request_json = _json(
            {"kind": request.kind, "idempotency_key": request.idempotency_key, "payload": request.payload},
            label="job request",
        )
        job_id = str(uuid.uuid4())
        result = await self.client.eval(
            _ENQUEUE_SCRIPT,
            3,
            self._key("pending"),
            self._key(f"idempotency:{request.idempotency_key}"),
            self._job_key(job_id),
            job_id,
            request.kind,
            request.idempotency_key,
            request_json,
        )
        return _text(result)

    async def claim(self, *, worker_id: str, lease_seconds: int) -> JobLease | None:
        if not worker_id.strip() or lease_seconds < 1:
            raise RedisJobQueueError("worker_id is required and lease_seconds must be positive")
        lease_id = secrets.token_urlsafe(24)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
        result = await self.client.eval(
            _CLAIM_SCRIPT,
            2,
            self._key("pending"),
            self._key("processing"),
            f"{lease_id}:{worker_id}",
            lease_seconds,
            worker_id,
            lease_id,
            int(expires_at.timestamp()),
            f"{self.prefix}:lease:",
            f"{self.prefix}:job:",
        )
        if result in (None, b"", ""):
            return None
        job_id = _text(result)
        record = { _text(key): _text(value) for key, value in (await self.client.hgetall(self._job_key(job_id))).items() }
        try:
            request = json.loads(record["request_json"])
            return JobLease(
                job_id=job_id,
                lease_id=record["lease_id"],
                worker_id=record["worker_id"],
                expires_at=datetime.fromtimestamp(int(record["lease_expires_at"]), timezone.utc),
                request=JobRequest(
                    kind=request["kind"],
                    idempotency_key=request["idempotency_key"],
                    payload=request["payload"],
                ),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RedisJobQueueError("claimed job record is corrupt") from exc

    async def complete(self, lease: JobLease, result: Mapping[str, Any]) -> None:
        result_json = _json(result, label="job result")
        ok = await self.client.eval(
            _COMPLETE_SCRIPT,
            3,
            self._lease_key(lease.job_id),
            self._key("processing"),
            self._job_key(lease.job_id),
            lease.job_id,
            f"{lease.lease_id}:{lease.worker_id}",
            result_json,
        )
        if not ok:
            raise RedisJobQueueError("job lease is missing, expired, or no longer running")

    async def fail(self, lease: JobLease, *, error_code: str, retryable: bool) -> None:
        if not error_code.strip() or any(character in error_code for character in "\r\n"):
            raise RedisJobQueueError("error_code must be a non-empty single-line value")
        attempts = await self.client.eval(
            _FAIL_SCRIPT,
            4,
            self._lease_key(lease.job_id),
            self._key("processing"),
            self._job_key(lease.job_id),
            self._key("pending"),
            lease.job_id,
            f"{lease.lease_id}:{lease.worker_id}",
            error_code,
            "1" if retryable else "0",
        )
        if not attempts:
            raise RedisJobQueueError("job lease is missing, expired, or no longer running")

    async def cancel(self, job_id: str) -> bool:
        if not job_id.strip():
            raise RedisJobQueueError("job_id must not be empty")
        result = await self.client.eval(
            _CANCEL_SCRIPT,
            4,
            self._lease_key(job_id),
            self._key("pending"),
            self._job_key(job_id),
            self._key("processing"),
            job_id,
        )
        return bool(result)


__all__ = ["RedisJobQueue", "RedisJobQueueError"]
