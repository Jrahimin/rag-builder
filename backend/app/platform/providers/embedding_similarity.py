"""Reusable raw embedding similarity and bounded passage helpers."""

from __future__ import annotations

import math
from dataclasses import dataclass

import regex

_TOKEN_PATTERN = regex.compile(r"\S+", regex.UNICODE)


@dataclass(frozen=True, slots=True)
class BoundedPassage:
    text: str
    char_start: int
    char_end: int
    token_count: int


def bounded_token_passages(
    text: str,
    *,
    window_tokens: int,
    overlap_tokens: int,
    minimum_tokens: int,
) -> list[BoundedPassage]:
    """Split text into overlapping token-like windows with stable offsets."""
    matches = list(_TOKEN_PATTERN.finditer(text))
    if not matches:
        return []
    if len(matches) <= window_tokens:
        return [
            BoundedPassage(
                text=text.strip(),
                char_start=matches[0].start(),
                char_end=matches[-1].end(),
                token_count=len(matches),
            )
        ]
    step = max(window_tokens - overlap_tokens, 1)
    passages: list[BoundedPassage] = []
    start = 0
    while start < len(matches):
        end = min(start + window_tokens, len(matches))
        if end - start < minimum_tokens and passages:
            break
        char_start = matches[start].start()
        char_end = matches[end - 1].end()
        passages.append(
            BoundedPassage(
                text=text[char_start:char_end],
                char_start=char_start,
                char_end=char_end,
                token_count=end - start,
            )
        )
        if end == len(matches):
            break
        start += step
    return passages


def cosine_similarity(left: list[float], right: list[float]) -> float:
    """Return raw cosine similarity on the natural -1..1 scale."""
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)
