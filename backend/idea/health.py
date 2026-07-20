from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Awaitable, Callable

import httpx

from .cache import CacheStore
from .config import AppConfig
from .database import Database


Probe = Callable[[], Awaitable[tuple[bool, str]]]


class HealthService:
    def __init__(
        self,
        config: AppConfig,
        database: Database,
        cache: CacheStore,
        *,
        google_patents_probe: Probe | None = None,
        workflow_recovery_ready: Callable[[], bool] | None = None,
    ):
        self.config = config
        self.database = database
        self.cache = cache
        self.google_patents_probe = google_patents_probe or self._probe_google_patents
        self.workflow_recovery_ready = workflow_recovery_ready or (lambda: False)

    async def check(self) -> dict:
        started = time.monotonic()
        database = self._check_database()
        cache = self._check_cache()
        model = self._check_model_auth()
        exa = self._check_exa_config()
        google = await self._timed_google_probe()
        recovery_ready = self.workflow_recovery_ready()
        workflow = {
            "ok": recovery_ready,
            "status": "ready" if recovery_ready else "pending",
            "detail": "recovery loop is ready" if recovery_ready else "workflow not connected yet",
        }
        provider_available = exa["ok"] or google["ok"] or self.config.search.providers.local_cache.enabled
        core_ok = database["ok"] and cache["ok"] and model["ok"]
        all_online = exa["ok"] and google["ok"]
        status = "ok" if core_ok and all_online and recovery_ready else "degraded"
        if not core_ok or not provider_available:
            status = "error"
        return {
            "ok": core_ok and provider_available,
            "status": status,
            "duration_ms": round((time.monotonic() - started) * 1000),
            "components": {
                "fastapi": {"ok": True, "status": "ready"},
                "configuration": {
                    "ok": True,
                    "status": "ready",
                    "source": str(self.config.source_path),
                },
                "database": database,
                "model": model,
                "exa_mcp": exa,
                "google_patents_local": google,
                "cache": cache,
                "workflow_recovery": workflow,
            },
        }

    def _check_database(self) -> dict:
        try:
            with self.database.connect() as connection:
                connection.execute("CREATE TEMP TABLE health_probe(value INTEGER)")
                connection.execute("INSERT INTO health_probe VALUES(1)")
                value = connection.execute("SELECT value FROM health_probe").fetchone()[0]
            return {"ok": value == 1, "status": "ready", "path": str(self.database.path)}
        except Exception as exc:
            return {"ok": False, "status": "error", "detail": type(exc).__name__}

    def _check_cache(self) -> dict:
        try:
            self.cache.root.mkdir(parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(prefix=".health-", dir=self.cache.root)
            os.close(descriptor)
            Path(name).unlink()
            stats = self.cache.stats()
            return {
                "ok": True,
                "status": "ready",
                "path": str(self.cache.root),
                "max_bytes": self.cache.max_bytes,
                **stats,
            }
        except Exception as exc:
            return {"ok": False, "status": "error", "detail": type(exc).__name__}

    def _check_model_auth(self) -> dict:
        return {
            "ok": True,
            "status": "runtime_required",
            "provider": self.config.model.provider,
            "model": self.config.model.default,
            "base_url": str(self.config.model.base_url),
            "credential_source": "per_run",
            "detail": "enter an API Token in the page for each browser session",
        }

    def _check_exa_config(self) -> dict:
        settings = self.config.search.providers.exa_mcp
        if not settings.enabled:
            return {"ok": False, "status": "disabled"}
        configured = bool(str(settings.endpoint)) and bool(
            settings.search_tool and settings.fetch_tool
        )
        return {
            "ok": configured,
            "status": "configured" if configured else "error",
            "detail": "unified configuration present; runtime calls are audited separately",
        }

    async def _probe_google_patents(self) -> tuple[bool, str]:
        settings = self.config.search.providers.google_patents_local
        if not settings.enabled:
            return False, "disabled"
        url = str(settings.base_url).rstrip("/") + "/"
        async def probe(*, trust_env: bool):
            async with httpx.AsyncClient(
                timeout=min(settings.timeout_seconds, 5),
                headers={"User-Agent": settings.user_agent},
                follow_redirects=True,
                trust_env=trust_env,
            ) as client:
                return await client.get(url)

        try:
            try:
                response = await probe(trust_env=settings.trust_environment_proxy)
            except (ImportError, httpx.ProxyError, httpx.ConnectError):
                if not settings.fallback_to_direct or not settings.trust_environment_proxy:
                    raise
                response = await probe(trust_env=False)
            return response.status_code < 500, f"HTTP {response.status_code}"
        except (ImportError, httpx.HTTPError) as exc:
            return False, type(exc).__name__

    async def _timed_google_probe(self) -> dict:
        started = time.monotonic()
        try:
            ok, detail = await self.google_patents_probe()
        except Exception as exc:
            ok, detail = False, type(exc).__name__
        return {
            "ok": ok,
            "status": "ready" if ok else "degraded",
            "detail": detail,
            "duration_ms": round((time.monotonic() - started) * 1000),
        }
