from __future__ import annotations

import re
from dataclasses import dataclass

from .direction_record import DirectionRecord
from .taxonomy import TaxonomyArtifact


@dataclass(frozen=True)
class TaxonomyCandidate:
    category_id: str
    score: float
    source: str = "LEXICAL_HIERARCHICAL"


def recall_taxonomy_candidates(
    direction: DirectionRecord,
    taxonomy: TaxonomyArtifact,
    *,
    top_k: int = 20,
) -> tuple[TaxonomyCandidate, ...]:
    if not 1 <= top_k <= 100: raise ValueError("top_k must be between 1 and 100")
    node_by_id = {node.category_id: node for node in taxonomy.nodes}
    allowed_parents = set(direction.candidate_level1_ids)
    text = " ".join((direction.technical_problem, direction.solution_mechanism, direction.technical_object, direction.direction_summary, *direction.keywords))
    query_tokens = _tokens(text)
    candidates=[]
    for leaf_id in taxonomy.leaf_category_ids:
        node=node_by_id[leaf_id]
        root_id=node.category_id
        while node_by_id[root_id].parent_id is not None:
            root_id=node_by_id[root_id].parent_id
        if allowed_parents and root_id not in allowed_parents: continue
        category_tokens=_tokens(" ".join(node.path))
        overlap=len(query_tokens & category_tokens)
        union=len(query_tokens | category_tokens) or 1
        score=overlap/union
        candidates.append(TaxonomyCandidate(leaf_id,score))
    candidates.sort(key=lambda item:(-item.score,item.category_id))
    return tuple(candidates[:top_k])


def _tokens(value: str) -> set[str]:
    lowered=value.casefold()
    words=set(re.findall(r"[a-z0-9]+",lowered))
    chinese="".join(re.findall(r"[\u3400-\u9fff]",lowered))
    grams={chinese[index:index+2] for index in range(max(0,len(chinese)-1))}
    grams.update(chinese[index:index+3] for index in range(max(0,len(chinese)-2)))
    return words|grams


__all__ = ["TaxonomyCandidate", "recall_taxonomy_candidates"]
