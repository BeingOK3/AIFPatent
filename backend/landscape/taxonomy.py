from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path


TAXONOMY_ARTIFACT_SCHEMA = "landscape-taxonomy/1.0.0"
_EXPECTED_HEADER = ("一级分类", "二级分类", "三级分类")
_ALIGNMENT_CELL = re.compile(r"^:?-{3,}:?$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class TaxonomyCompileError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class TaxonomyNode:
    category_id: str
    parent_id: str | None
    level: int
    name: str
    path: tuple[str, ...]
    is_leaf: bool
    sort_order: int


@dataclass(frozen=True, slots=True)
class TaxonomyArtifact:
    schema_version: str
    taxonomy_version: str
    taxonomy_hash: str
    source_hash: str
    source_row_count: int
    nodes: tuple[TaxonomyNode, ...]
    leaf_category_ids: tuple[str, ...]

    def canonical_payload(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "nodes": [_node_payload(node) for node in self.nodes],
            "leaf_category_ids": list(self.leaf_category_ids),
        }

    def to_dict(self) -> dict:
        payload = self.canonical_payload()
        payload.update(
            {
                "taxonomy_version": self.taxonomy_version,
                "taxonomy_hash": self.taxonomy_hash,
                "source_hash": self.source_hash,
                "source_row_count": self.source_row_count,
            }
        )
        return payload

    def canonical_json(self) -> str:
        return _canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, payload: dict) -> "TaxonomyArtifact":
        expected_keys = {
            "schema_version",
            "taxonomy_version",
            "taxonomy_hash",
            "source_hash",
            "source_row_count",
            "nodes",
            "leaf_category_ids",
        }
        if not isinstance(payload, dict) or set(payload) != expected_keys:
            raise TaxonomyCompileError("taxonomy artifact has an invalid top-level schema")
        try:
            nodes = tuple(_node_from_payload(item) for item in payload["nodes"])
            leaf_ids = tuple(payload["leaf_category_ids"])
            artifact = cls(
                schema_version=payload["schema_version"],
                taxonomy_version=payload["taxonomy_version"],
                taxonomy_hash=payload["taxonomy_hash"],
                source_hash=payload["source_hash"],
                source_row_count=payload["source_row_count"],
                nodes=nodes,
                leaf_category_ids=leaf_ids,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise TaxonomyCompileError("taxonomy artifact contains invalid values") from exc
        _validate_artifact(artifact)
        return artifact


def compile_taxonomy_file(path: str | Path) -> TaxonomyArtifact:
    source_path = Path(path)
    source = source_path.read_text(encoding="utf-8")
    return compile_taxonomy_markdown(source)


def compile_taxonomy_markdown(source: str) -> TaxonomyArtifact:
    if not isinstance(source, str) or not source.strip():
        raise TaxonomyCompileError("taxonomy source must be non-empty Markdown")

    rows = _parse_rows(source)
    node_builders: dict[tuple[str, ...], dict] = {}
    leaf_paths: list[tuple[str, ...]] = []
    seen_leaf_paths: set[tuple[str, ...]] = set()

    for row_order, (level1, level2, level3) in enumerate(rows, start=1):
        if not level1:
            raise TaxonomyCompileError(f"taxonomy row {row_order} has empty level 1")
        if not level2:
            raise TaxonomyCompileError(f"taxonomy row {row_order} has empty level 2")

        leaf_path = (level1, level2, level3) if level3 else (level1, level2)
        if leaf_path in seen_leaf_paths:
            raise TaxonomyCompileError(
                f"duplicate taxonomy path at row {row_order}: {' > '.join(leaf_path)}"
            )
        seen_leaf_paths.add(leaf_path)
        leaf_paths.append(leaf_path)

        for level in range(1, len(leaf_path) + 1):
            path = leaf_path[:level]
            if path not in node_builders:
                node_builders[path] = {
                    "category_id": _category_id(path),
                    "parent_id": _category_id(path[:-1]) if level > 1 else None,
                    "level": level,
                    "name": path[-1],
                    "path": path,
                    "sort_order": len(node_builders) + 1,
                }

    for leaf_path in leaf_paths:
        if any(
            len(other) > len(leaf_path) and other[: len(leaf_path)] == leaf_path
            for other in leaf_paths
        ):
            raise TaxonomyCompileError(
                f"taxonomy path cannot be both leaf and parent: {' > '.join(leaf_path)}"
            )

    child_paths = {path[:-1] for path in node_builders if len(path) > 1}
    nodes = tuple(
        TaxonomyNode(
            **builder,
            is_leaf=path not in child_paths,
        )
        for path, builder in node_builders.items()
    )
    _validate_category_id_uniqueness(nodes)

    leaf_ids = tuple(_category_id(path) for path in leaf_paths)
    canonical_payload = {
        "schema_version": TAXONOMY_ARTIFACT_SCHEMA,
        "nodes": [_node_payload(node) for node in nodes],
        "leaf_category_ids": list(leaf_ids),
    }
    taxonomy_hash = _sha256(_canonical_json(canonical_payload))
    artifact = TaxonomyArtifact(
        schema_version=TAXONOMY_ARTIFACT_SCHEMA,
        taxonomy_version=f"TAX-{taxonomy_hash[:16]}",
        taxonomy_hash=taxonomy_hash,
        source_hash=_sha256(source),
        source_row_count=len(rows),
        nodes=nodes,
        leaf_category_ids=leaf_ids,
    )
    _validate_artifact(artifact)
    return artifact


def _parse_rows(source: str) -> list[tuple[str, str, str]]:
    lines = source.splitlines()
    header_indexes = [
        index
        for index, line in enumerate(lines)
        if line.lstrip().startswith("|")
        and tuple(_normalize(cell) for cell in _split_row(line)) == _EXPECTED_HEADER
    ]
    if len(header_indexes) != 1:
        raise TaxonomyCompileError(
            f"taxonomy must contain exactly one {_EXPECTED_HEADER!r} table header"
        )

    header_index = header_indexes[0]
    if header_index + 1 >= len(lines):
        raise TaxonomyCompileError("taxonomy table is missing its alignment row")
    alignment = _split_row(lines[header_index + 1])
    if len(alignment) != 3 or not all(
        _ALIGNMENT_CELL.fullmatch(cell.strip()) for cell in alignment
    ):
        raise TaxonomyCompileError("taxonomy table has an invalid alignment row")

    rows: list[tuple[str, str, str]] = []
    for line_number, line in enumerate(lines[header_index + 2 :], start=header_index + 3):
        if not line.strip():
            continue
        if not line.lstrip().startswith("|"):
            raise TaxonomyCompileError(
                f"unparsed non-table content after taxonomy header at line {line_number}"
            )
        cells = _split_row(line)
        if len(cells) != 3:
            raise TaxonomyCompileError(
                f"taxonomy row at line {line_number} must contain exactly 3 columns"
            )
        rows.append(tuple(_normalize(cell) for cell in cells))
    if not rows:
        raise TaxonomyCompileError("taxonomy table must contain at least one data row")
    return rows


def _split_row(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        raise TaxonomyCompileError("taxonomy table rows must start and end with '|'")
    return stripped[1:-1].split("|")


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def _category_id(path: tuple[str, ...]) -> str:
    normalized_path = "\x1f".join(item.casefold() for item in path)
    return f"CAT-{_sha256(normalized_path)[:16]}"


def _node_payload(node: TaxonomyNode) -> dict:
    payload = asdict(node)
    payload["path"] = list(node.path)
    return payload


def _node_from_payload(payload: object) -> TaxonomyNode:
    expected_keys = {
        "category_id",
        "parent_id",
        "level",
        "name",
        "path",
        "is_leaf",
        "sort_order",
    }
    if not isinstance(payload, dict) or set(payload) != expected_keys:
        raise TaxonomyCompileError("taxonomy node has an invalid schema")
    path = payload["path"]
    if not isinstance(path, list) or not all(isinstance(item, str) for item in path):
        raise TaxonomyCompileError("taxonomy node path must be a string list")
    return TaxonomyNode(
        category_id=payload["category_id"],
        parent_id=payload["parent_id"],
        level=payload["level"],
        name=payload["name"],
        path=tuple(path),
        is_leaf=payload["is_leaf"],
        sort_order=payload["sort_order"],
    )


def _validate_category_id_uniqueness(nodes: tuple[TaxonomyNode, ...]) -> None:
    ids: dict[str, tuple[str, ...]] = {}
    for node in nodes:
        existing = ids.setdefault(node.category_id, node.path)
        if existing != node.path:
            raise TaxonomyCompileError(
                f"category ID collision: {' > '.join(existing)} and {' > '.join(node.path)}"
            )


def _validate_artifact(artifact: TaxonomyArtifact) -> None:
    if artifact.schema_version != TAXONOMY_ARTIFACT_SCHEMA:
        raise TaxonomyCompileError("unsupported taxonomy artifact schema")
    if not isinstance(artifact.source_row_count, int) or isinstance(
        artifact.source_row_count, bool
    ):
        raise TaxonomyCompileError("taxonomy source row count must be an integer")
    if artifact.source_row_count <= 0:
        raise TaxonomyCompileError("taxonomy source row count must be positive")
    if not isinstance(artifact.source_hash, str) or not _SHA256.fullmatch(
        artifact.source_hash
    ):
        raise TaxonomyCompileError("taxonomy source hash is invalid")
    if not isinstance(artifact.taxonomy_hash, str) or not _SHA256.fullmatch(
        artifact.taxonomy_hash
    ):
        raise TaxonomyCompileError("taxonomy hash is invalid")
    if artifact.taxonomy_version != f"TAX-{artifact.taxonomy_hash[:16]}":
        raise TaxonomyCompileError("taxonomy version does not match taxonomy hash")
    if not artifact.nodes:
        raise TaxonomyCompileError("taxonomy artifact must contain nodes")

    node_by_id: dict[str, TaxonomyNode] = {}
    path_by_id: dict[tuple[str, ...], TaxonomyNode] = {}
    for expected_order, node in enumerate(artifact.nodes, start=1):
        if not isinstance(node.level, int) or isinstance(node.level, bool):
            raise TaxonomyCompileError("taxonomy node level must be an integer")
        if not isinstance(node.sort_order, int) or isinstance(node.sort_order, bool):
            raise TaxonomyCompileError("taxonomy node sort order must be an integer")
        if node.sort_order != expected_order:
            raise TaxonomyCompileError("taxonomy node sort order must be contiguous")
        if not node.path or node.level != len(node.path) or node.level not in {1, 2, 3}:
            raise TaxonomyCompileError("taxonomy node level and path are inconsistent")
        if any(not item or item != _normalize(item) for item in node.path):
            raise TaxonomyCompileError("taxonomy node path is not normalized")
        if node.name != node.path[-1]:
            raise TaxonomyCompileError("taxonomy node name does not match its path")
        if node.category_id != _category_id(node.path):
            raise TaxonomyCompileError("taxonomy category ID does not match its path")
        expected_parent = _category_id(node.path[:-1]) if node.level > 1 else None
        if node.parent_id != expected_parent:
            raise TaxonomyCompileError("taxonomy parent ID does not match its path")
        if node.category_id in node_by_id or node.path in path_by_id:
            raise TaxonomyCompileError("taxonomy artifact contains duplicate nodes")
        node_by_id[node.category_id] = node
        path_by_id[node.path] = node

    child_paths = {node.path[:-1] for node in artifact.nodes if node.level > 1}
    for node in artifact.nodes:
        if node.is_leaf != (node.path not in child_paths):
            raise TaxonomyCompileError("taxonomy node leaf flag is inconsistent")
        if node.parent_id is not None and node.parent_id not in node_by_id:
            raise TaxonomyCompileError("taxonomy node references an unknown parent")

    if len(artifact.leaf_category_ids) != artifact.source_row_count:
        raise TaxonomyCompileError("taxonomy leaf count does not match source row count")
    if len(set(artifact.leaf_category_ids)) != len(artifact.leaf_category_ids):
        raise TaxonomyCompileError("taxonomy artifact contains duplicate leaf IDs")
    for category_id in artifact.leaf_category_ids:
        node = node_by_id.get(category_id)
        if node is None or not node.is_leaf:
            raise TaxonomyCompileError("taxonomy leaf list references a non-leaf node")

    expected_hash = _sha256(_canonical_json(artifact.canonical_payload()))
    if artifact.taxonomy_hash != expected_hash:
        raise TaxonomyCompileError("taxonomy artifact content hash mismatch")


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = [
    "TAXONOMY_ARTIFACT_SCHEMA",
    "TaxonomyArtifact",
    "TaxonomyCompileError",
    "TaxonomyNode",
    "compile_taxonomy_file",
    "compile_taxonomy_markdown",
]
