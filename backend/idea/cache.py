from __future__ import annotations

import contextlib
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .database import Database, now_ms
from .run_store import RunStore, sha256_bytes


CATEGORY_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


class CacheError(RuntimeError):
    pass


@dataclass(frozen=True)
class CleanupResult:
    before_bytes: int
    after_bytes: int
    removed_count: int
    removed_bytes: int
    failed_paths: tuple[str, ...] = ()


class CacheStore:
    """Rebuildable cache with insertion-ordered FIFO eviction and read leases."""

    def __init__(
        self,
        root: str | Path,
        database: Database,
        *,
        max_bytes: int,
        low_watermark_bytes: int,
        cleanup_after_write: bool = True,
    ):
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        if not 0 <= low_watermark_bytes <= max_bytes:
            raise ValueError("low watermark must be between zero and max_bytes")
        self.root = Path(root).resolve()
        self.database = database
        self.max_bytes = max_bytes
        self.low_watermark_bytes = low_watermark_bytes
        self.cleanup_after_write = cleanup_after_write
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _validate_category(category: str) -> str:
        if not CATEGORY_PATTERN.fullmatch(category):
            raise CacheError(f"invalid cache category: {category!r}")
        return category

    def _path_for(self, category: str, key: str) -> Path:
        category = self._validate_category(category)
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.root / category / digest[:2] / digest[2:]

    def put_bytes(self, key: str, category: str, content: bytes) -> Path | None:
        if not key:
            raise CacheError("cache key cannot be empty")
        if len(content) > self.max_bytes:
            return None
        path = self._path_for(category, key)
        content_hash = sha256_bytes(content)

        with self.database.connect() as connection:
            existing = connection.execute(
                "SELECT * FROM cache_entries WHERE cache_key = ?", (key,)
            ).fetchone()
            if existing is not None:
                if existing["content_hash"] != content_hash:
                    raise CacheError("cache key collision with different content")
                existing_path = Path(existing["path"])
                if not existing_path.is_file():
                    RunStore.write_bytes_atomic(existing_path, content)
                return existing_path

            sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM cache_entries"
            ).fetchone()[0]
            RunStore.write_bytes_atomic(path, content)
            try:
                connection.execute(
                    """
                    INSERT INTO cache_entries(
                        cache_key,category,path,size_bytes,content_hash,created_at,sequence,lease_count
                    ) VALUES(?,?,?,?,?,?,?,0)
                    """,
                    (key, category, str(path), len(content), content_hash, now_ms(), sequence),
                )
            except Exception:
                path.unlink(missing_ok=True)
                raise

        if self.cleanup_after_write:
            self.cleanup()
        return path if path.is_file() else None

    @contextlib.contextmanager
    def lease(self, key: str) -> Iterator[Path]:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT path FROM cache_entries WHERE cache_key = ?", (key,)
            ).fetchone()
            if row is None:
                raise KeyError(key)
            path = Path(row["path"])
            if not path.is_file():
                connection.execute("DELETE FROM cache_entries WHERE cache_key = ?", (key,))
                raise KeyError(key)
            connection.execute(
                "UPDATE cache_entries SET lease_count = lease_count + 1 WHERE cache_key = ?",
                (key,),
            )
        try:
            yield path
        finally:
            with self.database.connect() as connection:
                connection.execute(
                    """
                    UPDATE cache_entries
                    SET lease_count = CASE WHEN lease_count > 0 THEN lease_count - 1 ELSE 0 END
                    WHERE cache_key = ?
                    """,
                    (key,),
                )

    def cleanup(self, *, force: bool = False) -> CleanupResult:
        failed: list[str] = []
        removed_count = 0
        removed_bytes = 0
        with self.database.connect() as connection:
            before = connection.execute(
                "SELECT COALESCE(SUM(size_bytes), 0) FROM cache_entries"
            ).fetchone()[0]
            if not force and before <= self.max_bytes:
                return CleanupResult(before, before, 0, 0)
            target = self.low_watermark_bytes
            current = before
            candidates = connection.execute(
                """
                SELECT cache_key,path,size_bytes FROM cache_entries
                WHERE lease_count = 0 ORDER BY sequence ASC
                """
            ).fetchall()
            for row in candidates:
                if current <= target:
                    break
                path = Path(row["path"])
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    failed.append(str(path))
                    continue
                connection.execute(
                    "DELETE FROM cache_entries WHERE cache_key = ?", (row["cache_key"],)
                )
                current -= row["size_bytes"]
                removed_count += 1
                removed_bytes += row["size_bytes"]
            after = connection.execute(
                "SELECT COALESCE(SUM(size_bytes), 0) FROM cache_entries"
            ).fetchone()[0]
        self._remove_empty_directories()
        return CleanupResult(before, after, removed_count, removed_bytes, tuple(failed))

    def repair(self) -> dict[str, int]:
        missing_records = 0
        referenced: set[Path] = set()
        with self.database.connect() as connection:
            rows = connection.execute("SELECT cache_key,path FROM cache_entries").fetchall()
            for row in rows:
                path = Path(row["path"]).resolve()
                if self.root not in path.parents:
                    connection.execute(
                        "DELETE FROM cache_entries WHERE cache_key = ?", (row["cache_key"],)
                    )
                    missing_records += 1
                elif not path.is_file():
                    connection.execute(
                        "DELETE FROM cache_entries WHERE cache_key = ?", (row["cache_key"],)
                    )
                    missing_records += 1
                else:
                    referenced.add(path)

        orphan_files = 0
        for path in self.root.rglob("*"):
            if path.is_file() and path.resolve() not in referenced:
                path.unlink()
                orphan_files += 1
        self._remove_empty_directories()
        return {"missing_records": missing_records, "orphan_files": orphan_files}

    def stats(self) -> dict[str, int]:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS entry_count,
                       COALESCE(SUM(size_bytes), 0) AS size_bytes,
                       COALESCE(SUM(CASE WHEN lease_count > 0 THEN 1 ELSE 0 END), 0) AS leased_count
                FROM cache_entries
                """
            ).fetchone()
        return dict(row)

    def entries(self) -> list[dict]:
        with self.database.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM cache_entries ORDER BY sequence ASC"
                ).fetchall()
            ]

    def _remove_empty_directories(self) -> None:
        directories = sorted(
            (path for path in self.root.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        )
        for directory in directories:
            try:
                directory.rmdir()
            except OSError:
                pass
