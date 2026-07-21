#!/usr/bin/env python3
from __future__ import annotations

import argparse
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
REQUIRED_KEYS = (
    "AIFPATENT_POSTGRES_DB",
    "AIFPATENT_POSTGRES_USER",
    "AIFPATENT_POSTGRES_PASSWORD",
    "AIFPATENT_REDIS_PASSWORD",
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
        "AIFPATENT_REDIS_PASSWORD": _random_secret(),
        "AIFPATENT_REDIS_PORT": "6379",
        "AIFPATENT_MINIO_ROOT_USER": "aifpatent",
        "AIFPATENT_MINIO_ROOT_PASSWORD": _random_secret(),
        "AIFPATENT_MINIO_API_PORT": "9000",
        "AIFPATENT_MINIO_CONSOLE_PORT": "9001",
        "AIFPATENT_S3_BUCKET": "aifpatent-corpus",
        "AIFPATENT_S3_REGION": "us-east-1",
        "AIFPATENT_MINIO_VERSION": "RELEASE.2025-10-15T17-29-55Z",
        "AIFPATENT_APP_PORT": "8001",
        "AIFPATENT_PIP_INDEX_URL": "https://pypi.org/simple",
        "AIFPATENT_GOPROXY": "https://proxy.golang.org,direct",
    }
    return "".join(f"{key}={value}\n" for key, value in values.items())


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
        "up": ["up", "--detach", "--no-build", "--wait"],
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
            "-f /docker-entrypoint-initdb.d/020_corpus_schema.sql && "
            'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" '
            "-f /docker-entrypoint-initdb.d/030_lexical_schema.sql && "
            'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" '
            "-f /docker-entrypoint-initdb.d/035_report_retrieval_schema.sql",
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
                values.get("AIFPATENT_PIP_INDEX_URL", "https://pypi.org/simple"),
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
                values.get("AIFPATENT_GOPROXY", "https://proxy.golang.org,direct"),
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


def run(action: str) -> int:
    if shutil.which("docker") is None:
        raise InfraError("docker with the Compose plugin is required")
    created = ensure_environment()
    if created:
        print(f"created local credential file: {ENV_PATH.relative_to(PROJECT_ROOT)}")
    if action == "up":
        for service in ("app", "object-store"):
            built = subprocess.run(_build_command(service), cwd=PROJECT_ROOT, check=False)
            if built.returncode != 0:
                return built.returncode
    completed = subprocess.run(_docker_command(action), cwd=PROJECT_ROOT, check=False)
    if completed.returncode != 0 or action != "up":
        return completed.returncode
    bucket = subprocess.run(_docker_command("ensure-bucket"), cwd=PROJECT_ROOT, check=False)
    return bucket.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage the local PostgreSQL/Redis/S3-compatible RAG dependencies."
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
            return 0
        return run(arguments.action)
    except InfraError as exc:
        print(f"RAG infrastructure error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
