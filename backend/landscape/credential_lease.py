from __future__ import annotations

import time
from dataclasses import dataclass

from idea.model_client import RuntimeModelConfig


class CredentialLeaseError(RuntimeError):
    pass


@dataclass(frozen=True, repr=False)
class CredentialLease:
    run_id: str
    owner: str
    expires_at: int
    config: RuntimeModelConfig


class CredentialVault:
    def __init__(self, *, lease_ms: int = 300_000):
        if lease_ms < 1000: raise ValueError("lease_ms must be at least 1000")
        self.lease_ms = lease_ms
        self._configs: dict[str, RuntimeModelConfig] = {}
        self._leases: dict[str, CredentialLease] = {}

    def put(self, run_id: str, config: RuntimeModelConfig) -> None:
        if not run_id.strip() or not config.api_key.strip(): raise ValueError("run ID and API key must not be blank")
        self._configs[run_id] = config
        self._leases.pop(run_id, None)

    def acquire(self, run_id: str, owner: str, *, now: int | None = None) -> CredentialLease:
        if not owner.strip(): raise ValueError("credential lease owner must not be blank")
        current = int(time.time() * 1000) if now is None else now
        config = self._configs.get(run_id)
        if config is None: raise CredentialLeaseError("credentials are required for this Run")
        lease = self._leases.get(run_id)
        if lease and lease.expires_at > current and lease.owner != owner:
            raise CredentialLeaseError("credentials are leased by another worker")
        result = CredentialLease(run_id, owner, current + self.lease_ms, config)
        self._leases[run_id] = result
        return result

    def release(self, run_id: str, owner: str) -> None:
        lease = self._leases.get(run_id)
        if lease and lease.owner == owner: self._leases.pop(run_id, None)

    def revoke(self, run_id: str) -> None:
        self._leases.pop(run_id, None); self._configs.pop(run_id, None)

    def has_credentials(self, run_id: str) -> bool:
        return run_id in self._configs


__all__ = ["CredentialLease", "CredentialLeaseError", "CredentialVault"]
