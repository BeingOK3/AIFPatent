from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import rag_infra


class RagInfrastructureTests(unittest.TestCase):
    def test_environment_is_created_once_with_private_random_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rag.env"
            self.assertTrue(rag_infra.ensure_environment(path))
            first = path.read_text(encoding="utf-8")
            self.assertFalse(rag_infra.ensure_environment(path))
            self.assertEqual(path.read_text(encoding="utf-8"), first)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            values = rag_infra._parse_environment(path)
            self.assertGreaterEqual(len(values["AIFPATENT_POSTGRES_PASSWORD"]), 40)
            self.assertNotEqual(
                values["AIFPATENT_POSTGRES_PASSWORD"],
                values["AIFPATENT_REDIS_PASSWORD"],
            )

    def test_existing_environment_with_placeholder_or_open_permissions_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rag.env"
            path.write_text(
                (rag_infra.DEPLOY_ROOT / "rag.env.example").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            os.chmod(path, 0o600)
            with self.assertRaises(rag_infra.InfraError):
                rag_infra.ensure_environment(path)

            path.write_text(rag_infra._new_environment(), encoding="utf-8")
            os.chmod(path, 0o644)
            with self.assertRaises(rag_infra.InfraError):
                rag_infra.ensure_environment(path)

    def test_compose_commands_never_delete_volumes(self) -> None:
        down = rag_infra._docker_command("down")
        self.assertEqual(down[-1], "down")
        self.assertNotIn("--volumes", down)
        self.assertNotIn("-v", down)
        self.assertEqual(rag_infra._docker_command("check")[-2:], ["config", "--quiet"])

    def test_migrate_applies_additive_corpus_schema_without_exposing_credentials(self) -> None:
        command = rag_infra._docker_command("migrate")
        self.assertEqual(command[-4:-1], ["postgres", "sh", "-ec"])
        self.assertIn("020_corpus_schema.sql", command[-1])
        self.assertNotIn("$POSTGRES_PASSWORD", command[-1])
        self.assertNotIn("--volumes", command)

    def test_missing_docker_fails_before_environment_creation(self) -> None:
        with patch("tools.rag_infra.shutil.which", return_value=None), patch(
            "tools.rag_infra.ensure_environment"
        ) as ensure:
            with self.assertRaises(rag_infra.InfraError):
                rag_infra.run("check")
        ensure.assert_not_called()

    def test_compose_template_pins_services_and_contains_no_credentials(self) -> None:
        compose = rag_infra.COMPOSE_PATH.read_text(encoding="utf-8")
        self.assertIn("dockerfile: deploy/app/Dockerfile", compose)
        self.assertIn("app-data:/app/data", compose)
        self.assertIn("app-workspace:/app/workspace", compose)
        self.assertIn("app-logs:/app/logs", compose)
        self.assertIn("127.0.0.1:${AIFPATENT_APP_PORT:-8001}:8001", compose)
        self.assertIn("pgvector/pgvector:0.8.2-pg17-bookworm", compose)
        self.assertIn("redis:8.4.4-alpine", compose)
        self.assertIn("Dockerfile.minio", compose)
        self.assertIn("127.0.0.1:", compose)
        self.assertNotIn("replace-with-a-random", compose)
        self.assertNotIn("--volumes", compose)

    def test_new_environment_has_non_secret_application_build_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rag.env"
            rag_infra.ensure_environment(path)
            values = rag_infra._parse_environment(path)
            self.assertEqual(values["AIFPATENT_APP_PORT"], "8001")
            self.assertEqual(values["AIFPATENT_PIP_INDEX_URL"], "https://pypi.org/simple")

        init_sql = (rag_infra.DEPLOY_ROOT / "postgres-init" / "001_extensions.sql").read_text(
            encoding="utf-8"
        )
        self.assertIn("EXTENSION IF NOT EXISTS vector", init_sql)
        self.assertIn("EXTENSION IF NOT EXISTS pg_trgm", init_sql)


if __name__ == "__main__":
    unittest.main()
