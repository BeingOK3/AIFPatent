#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import stat
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_ROOT = PROJECT_ROOT / "deploy" / "rag"
COMPOSE_PATH = DEPLOY_ROOT / "compose.yml"
ENV_PATH = DEPLOY_ROOT / "rag.env"
PROVIDER_CREDENTIALS_PATH = PROJECT_ROOT / "config" / "provider-credentials.local.json"
REQUIRED_KEYS = (
    "AIFPATENT_POSTGRES_DB",
    "AIFPATENT_POSTGRES_USER",
    "AIFPATENT_POSTGRES_PASSWORD",
    "AIFPATENT_MINIO_ROOT_USER",
    "AIFPATENT_MINIO_ROOT_PASSWORD",
)


class InfraError(RuntimeError):
    pass


def _random_secret() -> str:
    return secrets.token_urlsafe(36)


def _new_environment() -> str:
    values = {
        "AIFPATENT_POSTGRES_DB": "aifpatent",
        "AIFPATENT_POSTGRES_USER": "aifpatent",
        "AIFPATENT_POSTGRES_PASSWORD": _random_secret(),
        "AIFPATENT_POSTGRES_PORT": "5432",
        "AIFPATENT_MINIO_ROOT_USER": "aifpatent",
        "AIFPATENT_MINIO_ROOT_PASSWORD": _random_secret(),
        "AIFPATENT_MINIO_API_PORT": "9000",
        "AIFPATENT_MINIO_CONSOLE_PORT": "9001",
        "AIFPATENT_S3_BUCKET": "aifpatent-corpus",
        "AIFPATENT_S3_REGION": "us-east-1",
        "AIFPATENT_MINIO_VERSION": "RELEASE.2025-10-15T17-29-55Z",
        "AIFPATENT_APP_PORT": "8001",
        "AIFPATENT_PIP_INDEX_URL": "https://mirrors.cloud.tencent.com/pypi/simple",
        "AIFPATENT_GOPROXY": "https://mirrors.tencent.com/go/,direct",
    }
    return "".join(f"{key}={value}\n" for key, value in values.items())


def _new_provider_credentials() -> str:
    return json.dumps({"serpapi": {"api_key": ""}}, ensure_ascii=False, indent=2) + "\n"


def ensure_provider_credentials(path: Path = PROVIDER_CREDENTIALS_PATH) -> bool:
    """Create an ignored, private local provider credential file once."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise InfraError(f"invalid local provider credential JSON: {path}") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("serpapi"), dict):
            raise InfraError("local provider credentials must contain a serpapi object")
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise InfraError("local provider credentials must use private permissions (0600)")
        return False
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(_new_provider_credentials())
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return True


def _parse_environment(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not key.strip():
            raise InfraError(f"invalid environment entry in {path.name}")
        values[key.strip()] = value.strip()
    return values


def _validate_environment(path: Path) -> None:
    values = _parse_environment(path)
    missing = [key for key in REQUIRED_KEYS if not values.get(key)]
    placeholders = [key for key, value in values.items() if "replace-with" in value]
    if missing or placeholders:
        names = sorted(set(missing + placeholders))
        raise InfraError("environment has missing or placeholder values: " + ", ".join(names))
    permissions = stat.S_IMODE(path.stat().st_mode)
    if permissions & 0o077:
        raise InfraError(f"{path} must not be readable or writable by group/others")


def ensure_environment(path: Path = ENV_PATH) -> bool:
    """Create local credentials once. Return True only when a new file was created."""

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        _validate_environment(path)
        return False
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(_new_environment())
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise
    _validate_environment(path)
    return True


def _docker_command(action: str) -> list[str]:
    prefix = [
        "docker",
        "compose",
        "--env-file",
        str(ENV_PATH),
        "--file",
        str(COMPOSE_PATH),
    ]
    commands = {
        # Retained for callers that inspect the legacy command shape. The
        # `up` action itself now uses the staged commands below.
        "up": ["up", "--detach", "--no-build", "--wait"],
        # Keep the application out of the first Compose transaction. New
        # application versions may require additive migrations at startup,
        # so dependencies must become ready before migrations run.
        "up-dependencies": [
            "up",
            "--detach",
            "--no-build",
            "--wait",
            "postgres",
            "object-store",
        ],
        "up-app": ["up", "--detach", "--no-build", "--wait", "app"],
        "down": ["down"],
        "status": ["ps"],
        "check": ["config", "--quiet"],
        # The init directory is only read when a Postgres volume is created.
        # Apply the current additive migration explicitly for existing volumes.
        "migrate": [
            "exec",
            "--no-TTY",
            "postgres",
            "sh",
            "-ec",
            'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" '
            "-f /docker-entrypoint-initdb.d/010_core_schema.sql && "
            'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" '
            "-f /docker-entrypoint-initdb.d/020_corpus_schema.sql && "
            'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" '
            "-f /docker-entrypoint-initdb.d/030_lexical_schema.sql && "
            'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" '
            "-f /docker-entrypoint-initdb.d/035_report_retrieval_schema.sql && "
            'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" '
            "-f /docker-entrypoint-initdb.d/040_report_citation_schema.sql && "
            'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" '
            "-f /docker-entrypoint-initdb.d/050_followup_schema.sql && "
            'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" '
            "-f /docker-entrypoint-initdb.d/055_report_hybrid_schema.sql && "
            'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" '
            "-f /docker-entrypoint-initdb.d/060_unified_runtime_schema.sql && "
            'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" '
            "-f /docker-entrypoint-initdb.d/070_landscape_company_analysis.sql && "
            'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" '
            "-f /docker-entrypoint-initdb.d/071_landscape_company_assignment_manifest.sql && "
            'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" '
            "-f /docker-entrypoint-initdb.d/072_landscape_document_fetches.sql && "
            'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" '
            "-f /docker-entrypoint-initdb.d/073_landscape_company_result_manifests.sql && "
            'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" '
            "-f /docker-entrypoint-initdb.d/074_landscape_keyed_steps.sql && "
            'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" '
            "-f /docker-entrypoint-initdb.d/075_landscape_repair_snapshots.sql && "
            'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" '
            "-f /docker-entrypoint-initdb.d/080_landscape_taxonomy.sql && "
            'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" '
            "-f /docker-entrypoint-initdb.d/081_landscape_v4_scope.sql && "
            'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" '
            "-f /docker-entrypoint-initdb.d/082_landscape_v4_company_registry.sql && "
            'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" '
            "-f /docker-entrypoint-initdb.d/083_landscape_v4_profile_versioning.sql && "
            'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" '
            "-f /docker-entrypoint-initdb.d/084_landscape_v4_company_name_order.sql && "
            'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" '
            "-f /docker-entrypoint-initdb.d/085_landscape_v4_scope_limitations.sql && "
            'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" '
            "-f /docker-entrypoint-initdb.d/086_landscape_v4_company_memory_actions.sql && "
            'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" '
            "-f /docker-entrypoint-initdb.d/087_landscape_v4_runs.sql",
        ],
        "ensure-bucket": [
            "exec",
            "--no-TTY",
            "app",
            "python",
            "-c",
            """import os
import boto3
from botocore.exceptions import ClientError

bucket = os.environ["AIFPATENT_S3_BUCKET"]
client = boto3.client(
    "s3",
    endpoint_url=os.environ["AIFPATENT_S3_ENDPOINT_URL"],
    aws_access_key_id=os.environ["AIFPATENT_S3_ACCESS_KEY"],
    aws_secret_access_key=os.environ["AIFPATENT_S3_SECRET_KEY"],
    region_name=os.environ.get("AIFPATENT_S3_REGION", "us-east-1"),
)
try:
    client.head_bucket(Bucket=bucket)
except ClientError as exc:
    code = str(exc.response.get("Error", {}).get("Code", ""))
    if code not in {"404", "NoSuchBucket", "NotFound"}:
        raise
    client.create_bucket(Bucket=bucket)
""",
        ],
    }
    return prefix + commands[action]


def _build_command(service: str) -> list[str]:
    values = _parse_environment(ENV_PATH)
    if service == "app":
        image = "aifpatent-rag-app"
        dockerfile = DEPLOY_ROOT.parent / "app" / "Dockerfile"
        args = [
            "--build-arg",
            "PIP_INDEX_URL="
            + os.environ.get(
                "AIFPATENT_PIP_INDEX_URL",
                values.get(
                    "AIFPATENT_PIP_INDEX_URL",
                    "https://mirrors.cloud.tencent.com/pypi/simple",
                ),
            ),
        ]
    elif service == "object-store":
        image = "aifpatent-rag-object-store"
        dockerfile = DEPLOY_ROOT / "Dockerfile.minio"
        args = [
            "--build-arg",
            "MINIO_VERSION="
            + values.get("AIFPATENT_MINIO_VERSION", "RELEASE.2025-10-15T17-29-55Z"),
            "--build-arg",
            "GOPROXY="
            + os.environ.get(
                "AIFPATENT_GOPROXY",
                values.get(
                    "AIFPATENT_GOPROXY",
                    "https://mirrors.tencent.com/go/,direct",
                ),
            ),
        ]
    else:
        raise InfraError(f"unsupported build service: {service}")
    return [
        "docker",
        "buildx",
        "build",
        "--allow",
        "network.host",
        "--network",
        "host",
        *args,
        "--file",
        str(dockerfile),
        "--tag",
        image,
        "--load",
        str(PROJECT_ROOT),
    ]


def _image_exists(image: str) -> bool:
    return subprocess.run(
        ["docker", "image", "inspect", image],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0


def _build_services(image_exists=_image_exists, *, force_object_store: bool = False) -> tuple[str, ...]:
    services = ["app"]
    if force_object_store or not image_exists("aifpatent-rag-object-store:latest"):
        services.append("object-store")
    return tuple(services)


def run(action: str) -> int:
    if shutil.which("docker") is None:
        raise InfraError("docker with the Compose plugin is required")
    created = ensure_environment()
    if created:
        print(f"created local credential file: {ENV_PATH.relative_to(PROJECT_ROOT)}")
    provider_created = ensure_provider_credentials()
    if provider_created:
        print(
            "created local provider credential file: "
            f"{PROVIDER_CREDENTIALS_PATH.relative_to(PROJECT_ROOT)}"
        )
    if action == "up":
        force_object_store = os.environ.get("AIFPATENT_REBUILD_OBJECT_STORE") == "1"
        for service in _build_services(force_object_store=force_object_store):
            built = subprocess.run(_build_command(service), cwd=PROJECT_ROOT, check=False)
            if built.returncode != 0:
                return built.returncode

        # Start only the services needed to execute migrations first. The app
        # is deliberately started in a second Compose transaction after the
        # schema is ready; otherwise startup hooks can query tables that do
        # not exist yet on an existing PostgreSQL volume.
        dependencies = subprocess.run(
            _docker_command("up-dependencies"), cwd=PROJECT_ROOT, check=False
        )
        if dependencies.returncode != 0:
            return dependencies.returncode
        migration = subprocess.run(
            _docker_command("migrate"), cwd=PROJECT_ROOT, check=False
        )
        if migration.returncode != 0:
            return migration.returncode
        completed = subprocess.run(
            _docker_command("up-app"), cwd=PROJECT_ROOT, check=False
        )
        if completed.returncode != 0:
            return completed.returncode
    else:
        completed = subprocess.run(_docker_command(action), cwd=PROJECT_ROOT, check=False)
        return completed.returncode
    bucket = subprocess.run(_docker_command("ensure-bucket"), cwd=PROJECT_ROOT, check=False)
    return bucket.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage the local PostgreSQL/S3-compatible RAG dependencies."
    )
    parser.add_argument("action", choices=("init", "up", "down", "status", "check", "migrate"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        if arguments.action == "init":
            created = ensure_environment()
            state = "created" if created else "already valid"
            print(f"local credential file {state}: {ENV_PATH.relative_to(PROJECT_ROOT)}")
            provider_created = ensure_provider_credentials()
            provider_state = "created" if provider_created else "already valid"
            print(
                f"local provider credential file {provider_state}: "
                f"{PROVIDER_CREDENTIALS_PATH.relative_to(PROJECT_ROOT)}"
            )
            return 0
        return run(arguments.action)
    except InfraError as exc:
        print(f"RAG infrastructure error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
