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
        "AIFPATENT_MINIO_VERSION": "RELEASE.2025-10-15T17-29-55Z",
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
        "up": ["up", "--detach", "--build", "--wait"],
        "down": ["down"],
        "status": ["ps"],
        "check": ["config", "--quiet"],
    }
    return prefix + commands[action]


def run(action: str) -> int:
    if shutil.which("docker") is None:
        raise InfraError("docker with the Compose plugin is required")
    created = ensure_environment()
    if created:
        print(f"created local credential file: {ENV_PATH.relative_to(PROJECT_ROOT)}")
    completed = subprocess.run(_docker_command(action), cwd=PROJECT_ROOT, check=False)
    return completed.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage the local PostgreSQL/Redis/S3-compatible RAG dependencies."
    )
    parser.add_argument("action", choices=("init", "up", "down", "status", "check"))
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
