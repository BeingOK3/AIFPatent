from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .database import assert_no_secrets
from .schemas import LandscapeScope


SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class LandscapeStoreError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class LandscapeRunPaths:
    root: Path
    input_json: Path
    report_json: Path
    report_md: Path
    patents_csv: Path
    manifest: Path


class LandscapeRunStore:
    """Durable, atomic report files for one independent landscape run."""

    def __init__(self, runs_dir: str | Path):
        self.runs_dir = Path(runs_dir).resolve()
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def paths(self, run_id: str) -> LandscapeRunPaths:
        if not SAFE_ID.fullmatch(run_id):
            raise LandscapeStoreError(f"unsafe run_id: {run_id!r}")
        root = self.runs_dir / run_id
        return LandscapeRunPaths(
            root=root,
            input_json=root / "input.json",
            report_json=root / "report.json",
            report_md=root / "report.md",
            patents_csv=root / "patents.csv",
            manifest=root / "manifest.json",
        )

    def initialize_run(
        self,
        run_id: str,
        *,
        scope: LandscapeScope,
        model: str,
        workflow_version: str,
        prompt_version: str,
    ) -> LandscapeRunPaths:
        paths = self.paths(run_id)
        paths.root.mkdir(parents=True, exist_ok=False)
        snapshot = {
            "run_id": run_id,
            "scope": scope.model_dump(mode="json"),
            "model": model,
            "workflow_version": workflow_version,
            "prompt_version": prompt_version,
        }
        assert_no_secrets(snapshot)
        self.write_json_atomic(paths.input_json, snapshot)
        return paths

    def write_reports(
        self,
        run_id: str,
        *,
        report: dict[str, Any],
        markdown: str,
        patents_csv: str,
        manifest_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        paths = self.paths(run_id)
        if not paths.input_json.is_file():
            raise LandscapeStoreError("input snapshot is required before reports")
        assert_no_secrets(report)
        assert_no_secrets(manifest_metadata)
        self.write_json_atomic(paths.report_json, report)
        self.write_text_atomic(paths.report_md, markdown)
        self.write_text_atomic(paths.patents_csv, patents_csv)
        files = [paths.input_json, paths.report_json, paths.report_md, paths.patents_csv]
        manifest = {
            "run_id": run_id,
            "metadata": manifest_metadata,
            "files": {
                path.name: {"size_bytes": path.stat().st_size, "sha256": sha256_file(path)}
                for path in files
            },
        }
        self.write_json_atomic(paths.manifest, manifest)
        return manifest

    def verify(self, run_id: str) -> dict[str, Any]:
        paths = self.paths(run_id)
        try:
            manifest = json.loads(paths.manifest.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            raise LandscapeStoreError("run manifest is missing or invalid") from exc
        if manifest.get("run_id") != run_id:
            raise LandscapeStoreError("run manifest identity mismatch")
        files = manifest.get("files")
        if not isinstance(files, dict) or not files:
            raise LandscapeStoreError("run manifest has no files")
        for relative, expected in files.items():
            path = (paths.root / relative).resolve()
            if paths.root.resolve() not in path.parents:
                raise LandscapeStoreError(f"manifest path escapes run directory: {relative}")
            if not path.is_file():
                raise LandscapeStoreError(f"manifest file is missing: {relative}")
            if path.stat().st_size != expected.get("size_bytes"):
                raise LandscapeStoreError(f"manifest size mismatch: {relative}")
            if sha256_file(path) != expected.get("sha256"):
                raise LandscapeStoreError(f"manifest hash mismatch: {relative}")
        return manifest

    def delete_run(self, run_id: str) -> None:
        paths = self.paths(run_id)
        if paths.root.exists():
            shutil.rmtree(paths.root)

    @staticmethod
    def write_json_atomic(path: Path, content: dict[str, Any]) -> None:
        encoded = json.dumps(content, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
        LandscapeRunStore.write_bytes_atomic(path, encoded + b"\n")

    @staticmethod
    def write_text_atomic(path: Path, content: str) -> None:
        LandscapeRunStore.write_bytes_atomic(path, content.encode("utf-8"))

    @staticmethod
    def write_bytes_atomic(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)
