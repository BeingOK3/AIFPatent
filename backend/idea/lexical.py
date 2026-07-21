from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .chunks import PatentChunk


LEXICAL_TOKENIZER_VERSION = "patent-lexical-v1"
_TERM = re.compile(r"[a-z0-9]+(?:[._+/-][a-z0-9]+)*|[\u3400-\u9fff]+", re.I)


@dataclass(frozen=True)
class LexicalTerms:
    value: str
    version: str = LEXICAL_TOKENIZER_VERSION


@dataclass(frozen=True)
class LexicalSearchRequest:
    query_id: str
    text: str
    allowed_version_ids: tuple[str, ...]
    section_types: tuple[str, ...] | None = None
    limit: int = 30

    def __post_init__(self) -> None:
        if not self.query_id.strip() or not self.text.strip():
            raise ValueError("lexical query ID and text must not be empty")
        if not self.allowed_version_ids:
            raise ValueError("lexical search requires allowed Version IDs")
        if len(set(self.allowed_version_ids)) != len(self.allowed_version_ids):
            raise ValueError("allowed Version IDs must be unique")
        if any(not value.strip() for value in self.allowed_version_ids):
            raise ValueError("allowed Version IDs must not be empty")
        if self.section_types is not None and (
            not self.section_types or any(not value.strip() for value in self.section_types)
        ):
            raise ValueError("section types must be omitted or non-empty")
        if not 1 <= self.limit <= 50:
            raise ValueError("lexical search limit must be between 1 and 50")


@dataclass(frozen=True)
class LexicalHit:
    query_id: str
    rank: int
    lexical_score: float
    match_kind: str
    chunk: PatentChunk


def normalize_lexical_text(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).lower().split())


def _tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for match in _TERM.finditer(normalize_lexical_text(text)):
        value = match.group(0)
        if re.fullmatch(r"[\u3400-\u9fff]+", value):
            tokens.append(value)
            if len(value) > 1:
                tokens.extend(value[index : index + 2] for index in range(len(value) - 1))
        else:
            tokens.append(value)
    return list(dict.fromkeys(tokens))


def _query_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for match in _TERM.finditer(normalize_lexical_text(text)):
        value = match.group(0)
        if re.fullmatch(r"[\u3400-\u9fff]+", value) and len(value) > 2:
            tokens.extend(value[index : index + 2] for index in range(len(value) - 1))
        else:
            tokens.append(value)
    return list(dict.fromkeys(tokens))


def lexical_search_terms(
    *,
    text: str,
    publication_number: str = "",
    section_type: str = "",
    section_label: str = "",
) -> LexicalTerms:
    source = " ".join((publication_number, section_type, section_label, text))
    return LexicalTerms(value=" ".join(_tokens(source)))


def lexical_query_terms(text: str) -> LexicalTerms:
    return LexicalTerms(value=" ".join(_query_tokens(text)))


__all__ = [
    "LEXICAL_TOKENIZER_VERSION",
    "LexicalHit",
    "LexicalSearchRequest",
    "LexicalTerms",
    "lexical_search_terms",
    "lexical_query_terms",
    "normalize_lexical_text",
]
