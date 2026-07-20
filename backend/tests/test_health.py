from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from pathlib import Path

from idea.cache import CacheStore
from idea.config import load_config
from idea.database import Database
from idea.health import HealthService


class HealthServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.root = root
        raw = json.loads(Path("config/ai4patent.json").read_text(encoding="utf-8"))
        raw["storage"]["database"] = str(root / "idea.db")
        raw["storage"]["langgraph_database"] = str(root / "langgraph.db")
        raw["storage"]["cache_dir"] = str(root / "cache")
        raw["storage"]["document_store_dir"] = str(root / "cache" / "documents")
        raw["storage"]["runs_dir"] = str(root / "runs")
        raw["storage"]["uploads_dir"] = str(root / "uploads")
        config_path = root / "config.json"
        config_path.write_text(json.dumps(raw), encoding="utf-8")
        self.config = load_config(config_path)
        self.db = Database(self.config.storage.database)
        self.db.initialize()
        self.cache = CacheStore(
            self.config.storage.cache_dir,
            self.db,
            max_bytes=self.config.storage.cache.max_bytes,
            low_watermark_bytes=self.config.storage.cache.low_watermark_bytes,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def service(self, google_ok=True, recovery=True):
        async def google_probe():
            return google_ok, "fixture"

        return HealthService(
            self.config,
            self.db,
            self.cache,
            google_patents_probe=google_probe,
            workflow_recovery_ready=lambda: recovery,
        )

    def test_all_components_ready(self) -> None:
        result = asyncio.run(self.service().check())
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["components"]["database"]["ok"])
        self.assertTrue(result["components"]["cache"]["ok"])
        self.assertEqual(result["components"]["langgraph_checkpointer"]["thread_key"], "run_id")
        self.assertEqual(result["components"]["model"]["credential_source"], "per_run")
        self.assertEqual(result["components"]["model"]["status"], "runtime_required")

    def test_one_online_provider_can_degrade_without_core_failure(self) -> None:
        result = asyncio.run(self.service(google_ok=False).check())
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "degraded")
        self.assertTrue(result["components"]["exa_mcp"]["ok"])
        self.assertFalse(result["components"]["google_patents_local"]["ok"])

    def test_idea_health_uses_unified_exa_config(self) -> None:
        async def google_probe():
            return True, "fixture"

        service = HealthService(
            self.config,
            self.db,
            self.cache,
            google_patents_probe=google_probe,
            workflow_recovery_ready=lambda: True,
        )
        result = asyncio.run(service.check())
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "ok")
        self.assertNotIn("opencode", result["components"])
        self.assertEqual(result["components"]["exa_mcp"]["status"], "configured")

    def test_missing_server_credential_waits_for_per_run_token(self) -> None:
        result = asyncio.run(self.service().check())
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["components"]["model"]["ok"])
        self.assertEqual(result["components"]["model"]["status"], "runtime_required")

    def test_pending_workflow_is_reported_without_hiding_other_health(self) -> None:
        result = asyncio.run(self.service(recovery=False).check())
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["components"]["workflow_recovery"]["status"], "pending")

    def test_health_never_returns_api_key(self) -> None:
        result = asyncio.run(self.service().check())
        rendered = json.dumps(result)
        self.assertNotIn("apiKey", rendered)

    def test_google_probe_falls_back_from_broken_proxy_to_direct(self) -> None:
        service = HealthService(
            self.config,
            self.db,
            self.cache,
            workflow_recovery_ready=lambda: True,
        )
        response = AsyncMock()
        response.status_code = 200
        with patch.object(
            service,
            "_probe_google_patents",
            wraps=service._probe_google_patents,
        ), patch("idea.health.httpx.AsyncClient") as client:
            proxied = AsyncMock()
            proxied.__aenter__.return_value.get.side_effect = ImportError("socks")
            direct = AsyncMock()
            direct.__aenter__.return_value.get.return_value = response
            client.side_effect = [proxied, direct]
            ok, detail = asyncio.run(service._probe_google_patents())
        self.assertTrue(ok)
        self.assertEqual(detail, "HTTP 200")
        self.assertEqual(client.call_args_list[1].kwargs["trust_env"], False)


if __name__ == "__main__":
    unittest.main()
