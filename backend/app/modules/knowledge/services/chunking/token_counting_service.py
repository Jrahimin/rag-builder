"""Approximate token counting for chunk sizing."""

from __future__ import annotations

from app.platform.domain.text_tokenization import tokenize


class TokenCountingService:
    """Deterministic approximate token counter shared across chunking."""

    method_name = "unicode_property_v1"

    def count(self, text: str) -> int:
        stripped = text.strip()
        if not stripped:
            return 0
        return len(tokenize(stripped))

    def fits(self, text: str, *, max_tokens: int) -> bool:
        return self.count(text) <= max_tokens
