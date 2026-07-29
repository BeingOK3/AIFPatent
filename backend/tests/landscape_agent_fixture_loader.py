from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "landscape_agent"


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def load_json(relative_path: str) -> Mapping[str, Any]:
    """Load one JSON fixture as an immutable mapping."""
    path = _fixture_path(relative_path, suffix=".json")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{relative_path} must contain a JSON object")
    return _freeze(value)


def load_jsonl(relative_path: str) -> tuple[Mapping[str, Any], ...]:
    """Load a JSONL fixture as immutable records without skipping blank lines."""
    path = _fixture_path(relative_path, suffix=".jsonl")
    records: list[Mapping[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            raise ValueError(f"{relative_path}:{line_number} is blank")
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{relative_path}:{line_number} must be a JSON object")
        records.append(_freeze(value))
    return tuple(records)


def iter_fixture_files() -> Iterator[Path]:
    yield from sorted(path for path in FIXTURE_ROOT.rglob("*") if path.is_file())


def _fixture_path(relative_path: str, *, suffix: str) -> Path:
    candidate = (FIXTURE_ROOT / relative_path).resolve()
    root = FIXTURE_ROOT.resolve()
    if candidate.parent != root and root not in candidate.parents:
        raise ValueError("fixture path must remain below landscape_agent")
    if candidate.suffix != suffix:
        raise ValueError(f"fixture path must end with {suffix}")
    return candidate
