from __future__ import annotations

import os
import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import rag_infra


class RagInfrastructureTests(unittest.TestCase):
    def test_provider_credentials_are_created_once_as_private_ignored_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "provider-credentials.local.json"
            self.assertTrue(rag_infra.ensure_provider_credentials(path))
            self.assertFalse(rag_infra.ensure_provider_credentials(path))
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload, {"serpapi": {"api_key": ""}})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_provider_credentials_with_open_permissions_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "provider-credentials.local.json"
            path.write_text('{"serpapi":{"api_key":"fixture"}}', encoding="utf-8")
            os.chmod(path, 0o644)
            with self.assertRaises(rag_infra.InfraError):
                rag_infra.ensure_provider_credentials(path)

    def test_up_reuses_cached_object_store_image_unless_forced(self) -> None:
        self.assertEqual(
            rag_infra._build_services(lambda _image: True, force_object_store=False),
            ("app",),
        )
        self.assertEqual(
            rag_infra._build_services(lambda _image: False, force_object_store=False),
            ("app", "object-store"),
        )
        self.assertEqual(
            rag_infra._build_services(lambda _image: True, force_object_store=True),
            ("app", "object-store"),
        )
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
            self.assertNotIn("AIFPATENT_REDIS_PASSWORD", values)

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
        self.assertIn("030_lexical_schema.sql", command[-1])
        self.assertIn("055_report_hybrid_schema.sql", command[-1])
        self.assertNotIn("$POSTGRES_PASSWORD", command[-1])
        self.assertNotIn("--volumes", command)

    def test_bucket_command_is_idempotent_and_uses_container_environment(self) -> None:
        command = rag_infra._docker_command("ensure-bucket")
        self.assertEqual(command[-4:-1], ["app", "python", "-c"])
        self.assertIn("head_bucket", command[-1])
        self.assertIn("create_bucket", command[-1])
        self.assertIn("AIFPATENT_S3_BUCKET", command[-1])
        self.assertNotIn("AIFPATENT_S3_SECRET_KEY=", " ".join(command))

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
        self.assertNotIn("app-data:/app/data", compose)
        self.assertIn("app-workspace:/app/workspace", compose)
        self.assertIn("app-logs:/app/logs", compose)
        self.assertIn("127.0.0.1:${AIFPATENT_APP_PORT:-8001}:8001", compose)
        self.assertIn("EMBEDDING_API_KEY: ${EMBEDDING_API_KEY:-}", compose)
        self.assertIn("AIFPATENT_EMBEDDING_ENABLED:", compose)
        self.assertIn("AIFPATENT_EMBEDDING_MODEL:", compose)
        self.assertIn("provider-credentials.local.json", compose)
        self.assertIn("/run/secrets/provider-credentials.json", compose)
        self.assertIn("/run/aifpatent/provider-credentials.json", compose)
        self.assertIn('entrypoint: ["/usr/local/bin/aifpatent-entrypoint"]', compose)
        self.assertIn('command: ["python", "-m", "uvicorn"', compose)
        self.assertIn("pgvector/pgvector:0.8.2-pg17-bookworm", compose)
        self.assertNotIn("redis:", compose)
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
        self.assertEqual(
            values["AIFPATENT_PIP_INDEX_URL"],
            "https://mirrors.cloud.tencent.com/pypi/simple",
        )
        self.assertEqual(
            values["AIFPATENT_GOPROXY"],
            "https://mirrors.tencent.com/go/,direct",
        )

    def test_up_builds_only_publicly_configured_images_before_no_build_compose_start(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment = Path(directory) / "rag.env"
            environment.write_text(rag_infra._new_environment(), encoding="utf-8")
            with patch.object(rag_infra, "ENV_PATH", environment):
                app = rag_infra._build_command("app")
                minio = rag_infra._build_command("object-store")
        self.assertIn("--allow", app)
        self.assertIn("network.host", app)
        self.assertIn("deploy/app/Dockerfile", " ".join(app))
        self.assertIn("deploy/rag/Dockerfile.minio", " ".join(minio))
        self.assertIn("--load", app)
        self.assertEqual(
            rag_infra._docker_command("up-dependencies")[-6:],
            ["up", "--detach", "--no-build", "--wait", "postgres", "object-store"],
        )
        self.assertEqual(
            rag_infra._docker_command("up-app")[-5:],
            ["up", "--detach", "--no-build", "--wait", "app"],
        )
        for command in (app, minio):
            joined = " ".join(command).upper()
            self.assertNotIn("PASSWORD", joined)

        init_sql = (rag_infra.DEPLOY_ROOT / "postgres-init" / "001_extensions.sql").read_text(
            encoding="utf-8"
        )
        self.assertIn("EXTENSION IF NOT EXISTS vector", init_sql)
        self.assertIn("EXTENSION IF NOT EXISTS pg_trgm", init_sql)

        migrate = " ".join(rag_infra._docker_command("migrate"))
        self.assertIn("030_lexical_schema.sql", migrate)
        self.assertIn("035_report_retrieval_schema.sql", migrate)
        self.assertIn("040_report_citation_schema.sql", migrate)
        self.assertIn("055_report_hybrid_schema.sql", migrate)
        self.assertIn("060_unified_runtime_schema.sql", migrate)
        self.assertIn("070_landscape_company_analysis.sql", migrate)
        self.assertIn("071_landscape_company_assignment_manifest.sql", migrate)
        self.assertIn("072_landscape_document_fetches.sql", migrate)
        self.assertIn("073_landscape_company_result_manifests.sql", migrate)
        self.assertIn("074_landscape_keyed_steps.sql", migrate)
        self.assertIn("075_landscape_repair_snapshots.sql", migrate)
        self.assertIn("080_landscape_taxonomy.sql", migrate)

    def test_up_migrates_between_dependency_and_application_start(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            environment = Path(directory) / "rag.env"
            environment.write_text(rag_infra._new_environment(), encoding="utf-8")
            commands: list[list[str]] = []

            def fake_run(command, **_kwargs):
                commands.append(command)
                return type("Result", (), {"returncode": 0})()

            with (
                patch.object(rag_infra, "ENV_PATH", environment),
                patch.object(rag_infra, "_build_services", return_value=()),
                patch.object(rag_infra.subprocess, "run", side_effect=fake_run),
            ):
                self.assertEqual(rag_infra.run("up"), 0)

        self.assertEqual(
            commands[0][-6:],
            ["up", "--detach", "--no-build", "--wait", "postgres", "object-store"],
        )
        self.assertIn("050_followup_schema.sql", commands[1][-1])
        self.assertEqual(
            commands[2][-5:], ["up", "--detach", "--no-build", "--wait", "app"]
        )
        self.assertEqual(commands[3][-4:-1], ["app", "python", "-c"])


if __name__ == "__main__":
    unittest.main()
