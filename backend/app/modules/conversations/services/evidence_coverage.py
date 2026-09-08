"""Validate a bounded completeness verdict against the exact final context."""

from __future__ import annotations

import unicodedata

import regex
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.conversations.ports import ContextChunk

# Up to four rule facets with separate user/source-language discovery routes.
MAX_REPAIR_DEPENDENCIES = 8
MAX_REPAIR_FOLLOWUPS = 2


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
    quote: str = Field(default="", max_length=6000)
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def require_quote_or_range(self) -> _Quote:
        has_range = self.start_line is not None or self.end_line is not None
        if has_range:
            if self.start_line is None or self.end_line is None or self.end_line < self.start_line:
                raise ValueError("A source range requires ordered start_line and end_line")
            if self.quote:
                raise ValueError("Select a source range or provide a quote, not both")
        elif not self.quote.strip():
            raise ValueError("A source quote or line range is required")
        return self


def numbered_source_lines(content: str) -> str:
    """Stable selectors for exact original lines, including empty lines."""
    return "\n".join(f"L{i}: {line}" for i, line in enumerate(content.splitlines(), start=1))


class _Check(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query_index: int
    supported: bool
    needs_adjacent_context: bool = False
    evidence: list[_Quote] = Field(max_length=8)


class CoverageVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    complete: bool
    missing: list[str] = Field(max_length=12)
    checks: list[_Check] = Field(max_length=MAX_REPAIR_DEPENDENCIES + 2 * MAX_REPAIR_FOLLOWUPS)

    @model_validator(mode="after")
    def require_consistent_completion(self) -> CoverageVerdict:
        if self.complete and (
            self.missing
            or not self.checks
            or any(not c.supported or not c.evidence for c in self.checks)
        ):
            raise ValueError(
                "A complete verdict must have no missing requirements and source evidence for "
                "every check. Unsuccessful discovery routes may cite another route's governing "
                "evidence; otherwise mark the verdict incomplete."
            )
        return self

    def resolve_source_ranges(self, context: list[ContextChunk]) -> bool:
        """Materialize model-selected ranges; never approximate a transcribed quotation."""
        sources = {str(c.chunk_id): c.content.splitlines(keepends=True) for c in context}
        for check in self.checks:
            for item in check.evidence:
                if item.start_line is None:
                    continue
                lines = sources.get(item.chunk_id)
                if lines is None or item.end_line is None or item.end_line > len(lines):
                    return False
                quote = "".join(lines[item.start_line - 1 : item.end_line])
                if not quote.strip():
                    return False
                item.quote = quote
                item.start_line = None
                item.end_line = None
        return True

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
