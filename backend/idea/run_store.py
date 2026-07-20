from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class RunStoreError(RuntimeError):
    pass


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_component(value: str, label: str) -> str:
    if not SAFE_ID.fullmatch(value):
        raise RunStoreError(f"unsafe {label}: {value!r}")
    return value


@dataclass(frozen=True)
class RunPaths:
    root: Path
    input_dir: Path
    artifacts_dir: Path
    report_json: Path
    report_md: Path
    manifest: Path


class RunStore:
    """Durable per-run files. This store is never subject to cache eviction."""

    def __init__(self, runs_dir: str | Path):
        self.runs_dir = Path(runs_dir).resolve()
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def paths(self, case_id: str, run_id: str) -> RunPaths:
        case = _safe_component(case_id, "case_id")
        run = _safe_component(run_id, "run_id")
        root = self.runs_dir / case / run
        return RunPaths(
            root=root,
            input_dir=root / "input",
            artifacts_dir=root / "artifacts",
            report_json=root / "report.json",
            report_md=root / "report.md",
            manifest=root / "manifest.json",
        )

    def initialize_run(self, case_id: str, run_id: str) -> RunPaths:
        paths = self.paths(case_id, run_id)
        paths.input_dir.mkdir(parents=True, exist_ok=False)
        paths.artifacts_dir.mkdir(parents=True, exist_ok=False)
        return paths

    def snapshot_input(
        self,
        case_id: str,
        run_id: str,
        *,
        input_text: str,
        metadata: dict[str, Any],
        attachments: Iterable[Path] = (),
    ) -> dict[str, Any]:
        paths = self.paths(case_id, run_id)
        if not paths.input_dir.is_dir():
            raise RunStoreError("run layout has not been initialized")

        idea_path = paths.input_dir / "idea.txt"
        self.write_text_atomic(idea_path, input_text)
        attachment_records: list[dict[str, Any]] = []
        names: set[str] = set()
        for source in attachments:
            source = Path(source)
            if not source.is_file():
                raise RunStoreError(f"attachment does not exist: {source.name}")
            name = source.name
            if name in names:
                raise RunStoreError(f"duplicate attachment name: {name}")
            names.add(name)
            destination = paths.input_dir / name
            self._copy_atomic(source, destination)
            attachment_records.append(
                {
                    "name": name,
                    "size_bytes": destination.stat().st_size,
                    "sha256": sha256_file(destination),
                }
            )

        input_record = {
            "idea": {
                "path": "input/idea.txt",
                "size_bytes": idea_path.stat().st_size,
                "sha256": sha256_file(idea_path),
            },
            "attachments": attachment_records,
            "metadata": metadata,
        }
        self.write_json_atomic(paths.input_dir / "input.json", input_record)
        return input_record

    def write_reports(
        self,
        case_id: str,
        run_id: str,
        *,
        report: dict[str, Any],
        markdown: str,
        manifest_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        paths = self.paths(case_id, run_id)
        if not (paths.input_dir / "input.json").is_file():
            raise RunStoreError("input snapshot is required before reports")

        self.write_json_atomic(paths.report_json, report)
        self.write_text_atomic(paths.report_md, markdown)
        input_files = sorted(path for path in paths.input_dir.iterdir() if path.is_file())
        manifest = {
            "case_id": case_id,
            "run_id": run_id,
            "metadata": manifest_metadata,
            "files": {
                str(path.relative_to(paths.root)): {
                    "size_bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
                for path in [*input_files, paths.report_json, paths.report_md]
            },
        }
        # Manifest is deliberately last: its presence means every referenced file was durable.
        self.write_json_atomic(paths.manifest, manifest)
        return manifest

    def verify(self, case_id: str, run_id: str) -> dict[str, Any]:
        paths = self.paths(case_id, run_id)
        try:
            manifest = json.loads(paths.manifest.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            raise RunStoreError("run manifest is missing or invalid") from exc
        if manifest.get("case_id") != case_id or manifest.get("run_id") != run_id:
            raise RunStoreError("run manifest identity mismatch")
        files = manifest.get("files")
        if not isinstance(files, dict) or not files:
            raise RunStoreError("run manifest has no files")
        for relative, expected in files.items():
            path = (paths.root / relative).resolve()
            if paths.root.resolve() not in path.parents:
                raise RunStoreError(f"manifest path escapes run directory: {relative}")
            if not path.is_file():
                raise RunStoreError(f"manifest file is missing: {relative}")
            if path.stat().st_size != expected.get("size_bytes"):
                raise RunStoreError(f"manifest size mismatch: {relative}")
            if sha256_file(path) != expected.get("sha256"):
                raise RunStoreError(f"manifest hash mismatch: {relative}")
        return manifest

    def delete_run(self, case_id: str, run_id: str) -> None:
        paths = self.paths(case_id, run_id)
        if paths.root.exists():
            shutil.rmtree(paths.root)
        case_dir = paths.root.parent
        if case_dir.is_dir() and not any(case_dir.iterdir()):
            case_dir.rmdir()

    def delete_case(self, case_id: str) -> None:
        case = _safe_component(case_id, "case_id")
        path = self.runs_dir / case
        if path.exists():
            shutil.rmtree(path)

    @staticmethod
    def write_text_atomic(path: Path, content: str) -> None:
        RunStore.write_bytes_atomic(path, content.encode("utf-8"))

    @staticmethod
    def write_json_atomic(path: Path, content: dict[str, Any]) -> None:
        encoded = json.dumps(content, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
        RunStore.write_bytes_atomic(path, encoded + b"\n")

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

    @staticmethod
    def _copy_atomic(source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", dir=destination.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            shutil.copyfile(source, temporary)
            with temporary.open("rb") as handle:
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
