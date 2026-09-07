"""Validate a bounded completeness verdict against the exact final context."""

from __future__ import annotations

import unicodedata

import regex
from pydantic import BaseModel, ConfigDict, Field

from app.modules.conversations.ports import ContextChunk

MAX_REPAIR_DEPENDENCIES = 4


def _quote_tokens(value: str) -> tuple[str, ...]:
    # OCR line wrapping and spaces around punctuation are presentation, not
    # different evidence. Preserve every word, numeral and punctuation token;
    # in particular "1 5" must never become "15", nor 15% become 10%.
    return tuple(regex.findall(r"[\p{L}\p{M}\p{N}_]+|[^\s]", unicodedata.normalize("NFC", value)))


def _contains_quote(source: tuple[str, ...], quote: str) -> bool:
    tokens = _quote_tokens(quote)
    return bool(tokens) and any(
        source[start : start + len(tokens)] == tokens
        for start in range(len(source) - len(tokens) + 1)
    )


class _Quote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chunk_id: str
    quote: str = Field(min_length=1, max_length=3000)


class _Check(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query_index: int
    supported: bool
    evidence: list[_Quote] = Field(max_length=8)


class CoverageVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    complete: bool
    missing: list[str] = Field(max_length=12)
    checks: list[_Check] = Field(max_length=MAX_REPAIR_DEPENDENCIES)

    def validates(self, groups: list[list[ContextChunk]], context: list[ContextChunk]) -> bool:
        if not self.complete or self.missing or len(self.checks) != len(groups):
            return False
        if {c.query_index for c in self.checks} != set(range(len(groups))):
            return False
        sources = {str(c.chunk_id): _quote_tokens(c.content) for c in context}
        for check in self.checks:
            if not check.supported or not check.evidence:
                return False
            for item in check.evidence:
                if (
                    item.chunk_id not in sources
                    or not item.quote.strip()
                    or not _contains_quote(sources[item.chunk_id], item.quote)
                ):
                    return False
        return True
