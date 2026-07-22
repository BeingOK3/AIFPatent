from __future__ import annotations

import re
from pathlib import Path


_QUERY_API_KEY = re.compile(r"(?i)(api_key=)(?!\[REDACTED\])[^&\s\"]+")


def redact_query_credentials(path: Path) -> int:
    """Redact query-string API keys left by older HTTP client INFO logs."""
    try:
        original = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return 0
    redacted, count = _QUERY_API_KEY.subn(r"\1[REDACTED]", original)
    if count:
        path.write_text(redacted, encoding="utf-8")
    return count
