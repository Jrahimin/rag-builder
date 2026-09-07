"""Validate a bounded completeness verdict against the exact final context."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.modules.conversations.ports import ContextChunk


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
    checks: list[_Check] = Field(max_length=3)

    def validates(self, groups: list[list[ContextChunk]], context: list[ContextChunk]) -> bool:
        if not self.complete or self.missing or len(self.checks) != len(groups):
            return False
        if {c.query_index for c in self.checks} != set(range(len(groups))):
            return False
        sources = {str(c.chunk_id): c.content for c in context}
        for check in self.checks:
            allowed = {str(c.chunk_id) for c in groups[check.query_index]}
            if not check.supported or not check.evidence:
                return False
            for item in check.evidence:
                if (
                    item.chunk_id not in allowed
                    or item.chunk_id not in sources
                    or not item.quote.strip()
                    or item.quote not in sources[item.chunk_id]
                ):
                    return False
        return True
