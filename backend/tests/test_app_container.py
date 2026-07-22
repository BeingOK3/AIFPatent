from __future__ import annotations

import unittest
from pathlib import Path


DOCKERFILE = Path("deploy/app/Dockerfile")
DOCKERIGNORE = Path(".dockerignore")
ENTRYPOINT = Path("deploy/app/container-entrypoint.sh")
MAIN = Path("backend/main.py")


class ApplicationContainerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dockerfile = DOCKERFILE.read_text(encoding="utf-8")
        self.dockerignore = DOCKERIGNORE.read_text(encoding="utf-8")
        self.entrypoint = ENTRYPOINT.read_text(encoding="utf-8")
        self.main = MAIN.read_text(encoding="utf-8")

    def test_runtime_is_pinned_non_root_and_single_worker(self) -> None:
        self.assertIn("python:3.12.13-slim-bookworm@sha256:", self.dockerfile)
        self.assertIn("USER 10001:10001", self.dockerfile)
        self.assertIn('"--workers", "1"', self.dockerfile)
        self.assertIn("/openapi.json", self.dockerfile)
        self.assertNotIn("API_KEY", self.dockerfile.upper())
        self.assertNotIn("COPY . ", self.dockerfile)

    def test_secret_entrypoint_copies_privately_then_drops_root(self) -> None:
        self.assertIn("install -m 0400 -o 10001 -g 10001", self.entrypoint)
        self.assertIn("setpriv --reuid=10001 --regid=10001", self.entrypoint)
        self.assertNotIn("api_key", self.entrypoint.lower())

    def test_http_client_does_not_log_complete_credential_urls(self) -> None:
        self.assertIn('logging.getLogger("httpx").setLevel(logging.WARNING)', self.main)
        self.assertIn('logging.getLogger("httpcore").setLevel(logging.WARNING)', self.main)

    def test_runtime_directories_and_assets_are_present(self) -> None:
        for path in (
            "/app/data/aifpatent",
            "/app/data/langgraph",
            "/app/logs",
            "/app/workspace/cache",
            "/app/workspace/idea-runs",
            "/app/workspace/uploads",
        ):
            self.assertIn(path, self.dockerfile)
        self.assertIn("backend /app/backend", self.dockerfile)
        self.assertIn("frontend /app/frontend", self.dockerfile)
        self.assertIn("config /app/config", self.dockerfile)

    def test_build_context_excludes_runtime_data_and_secrets(self) -> None:
        for entry in (
            ".git",
            "backend/.venv",
            "data",
            "workspace",
            "logs",
            ".env.*",
            "deploy/rag/rag.env",
            "config/provider-credentials.local.json",
        ):
            self.assertIn(entry, self.dockerignore)


if __name__ == "__main__":
    unittest.main()
