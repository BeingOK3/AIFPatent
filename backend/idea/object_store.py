from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path, PurePosixPath

from .ports import ObjectInfo


class ObjectStoreError(RuntimeError):
    pass


class FileObjectStore:
    """Atomic, immutable local implementation of the ObjectStore port."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        if not key or "\x00" in key or "\\" in key:
            raise ObjectStoreError("object key contains invalid characters")
        relative = PurePosixPath(key)
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            raise ObjectStoreError("object key must be a normalized relative path")
        path = self.root.joinpath(*relative.parts)
        try:
            path.parent.resolve(strict=False).relative_to(self.root)
        except ValueError as exc:
            raise ObjectStoreError("object key escapes the object store root") from exc
        return path

    @staticmethod
    def _hash(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def _metadata_path(path: Path) -> Path:
        return path.with_name(path.name + ".meta")

    def _object_info(self, key: str, path: Path) -> ObjectInfo:
        content = path.read_bytes()
        content_type = "application/octet-stream"
        encoding = "identity"
        metadata_path = self._metadata_path(path)
        if metadata_path.is_symlink():
            raise ObjectStoreError("object metadata cannot be a symlink")
        if metadata_path.exists():
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                content_type = str(metadata["content_type"])
                encoding = str(metadata["encoding"])
            except (OSError, KeyError, TypeError, ValueError) as exc:
                raise ObjectStoreError("object metadata is corrupt") from exc
        return ObjectInfo(
            key=key,
            size=len(content),
            sha256=self._hash(content),
            content_type=content_type,
            encoding=encoding,
        )

    def _write_metadata(self, path: Path, *, content_type: str, encoding: str) -> None:
        metadata_path = self._metadata_path(path)
        if metadata_path.is_symlink():
            raise ObjectStoreError("object metadata cannot be a symlink")
        metadata = json.dumps(
            {"content_type": content_type, "encoding": encoding},
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
        temporary_name: str | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".metadata-", dir=metadata_path.parent
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(metadata)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary_name, metadata_path, follow_symlinks=False)
            except FileExistsError:
                return
            os.chmod(metadata_path, 0o600)
        finally:
            if temporary_name:
                Path(temporary_name).unlink(missing_ok=True)

    async def put_if_absent(
        self,
        key: str,
        content: bytes,
        *,
        expected_sha256: str,
        content_type: str,
        encoding: str,
    ) -> ObjectInfo:
        actual_sha256 = self._hash(content)
        if actual_sha256 != expected_sha256:
            raise ObjectStoreError("content hash does not match expected_sha256")
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            if target.is_symlink():
                raise ObjectStoreError("object target cannot be a symlink")
            existing = self._object_info(key, target)
            if existing.sha256 != expected_sha256:
                raise ObjectStoreError("immutable object key already contains different content")
            return existing

        temporary_name: str | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".object-", dir=target.parent
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary_name, target, follow_symlinks=False)
            except FileExistsError:
                if target.is_symlink():
                    raise ObjectStoreError("object target cannot be a symlink")
                existing = self._object_info(key, target)
                if existing.sha256 != expected_sha256:
                    raise ObjectStoreError(
                        "concurrent writer created different immutable object content"
                    )
            else:
                os.chmod(target, 0o600)
                self._write_metadata(target, content_type=content_type, encoding=encoding)
                directory_fd = os.open(target.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            if temporary_name:
                Path(temporary_name).unlink(missing_ok=True)
        return self._object_info(key, target)

    async def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise ObjectStoreError(f"object does not exist: {key}") from exc
        except IsADirectoryError as exc:
            raise ObjectStoreError(f"object is not a file: {key}") from exc

    async def stat(self, key: str) -> ObjectInfo | None:
        path = self._path(key)
        if not path.exists():
            if path.is_symlink():
                raise ObjectStoreError("object target cannot be a symlink")
            return None
        if path.is_symlink() or not path.is_file():
            raise ObjectStoreError("object target must be a regular file")
        return self._object_info(key, path)


__all__ = ["FileObjectStore", "ObjectStoreError"]
