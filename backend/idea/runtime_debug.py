from __future__ import annotations

import json
import re
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any


_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "password",
    "secret",
    "token",
}


class RunDebugLog:
    """Append-only, Git-ignored operational events with defensive redaction."""

    def __init__(self, root: Path):
        self.root = root
        self._lock = threading.Lock()

    def append(self, run_id: str, event: str, **details: Any) -> None:
        record = {
            "timestamp_ms": round(time.time() * 1000),
            "event": event,
            "details": self.sanitize(details),
        }
        path = self.path(run_id)
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    def read(self, run_id: str, *, limit: int = 500) -> list[dict[str, Any]]:
        path = self.path(run_id)
        if not path.is_file():
            return []
        records: deque[dict[str, Any]] = deque(maxlen=max(1, min(limit, 2000)))
        try:
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    try:
                        value = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(value, dict):
                        records.append(self.sanitize(value))
        except OSError:
            return []
        return list(records)

    def path(self, run_id: str) -> Path:
        safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", run_id).replace("..", "_")
        return self.root / f"{safe_id}.jsonl"

    @classmethod
    def sanitize(cls, value: Any) -> Any:
        if isinstance(value, dict):
            sanitized = {}
            for key, item in value.items():
                normalized = str(key).lower().replace("-", "_")
                if normalized in _SENSITIVE_KEYS or normalized.endswith("_api_key"):
                    sanitized[key] = "[REDACTED]"
                else:
                    sanitized[key] = cls.sanitize(item)
            return sanitized
        if isinstance(value, (list, tuple)):
            return [cls.sanitize(item) for item in value]
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)
