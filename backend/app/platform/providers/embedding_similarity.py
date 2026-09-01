"""Reusable raw embedding similarity and bounded passage helpers."""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass

import regex

from app.platform.providers.contracts.embedding import BaseEmbeddingProvider, EmbeddingPurpose

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


async def score_best_passages(
    *,
    embedder: BaseEmbeddingProvider,
    query_vector: list[float],
    texts: dict[uuid.UUID, str],
    window_tokens: int,
    overlap_tokens: int,
    minimum_tokens: int,
) -> dict[uuid.UUID, tuple[float, BoundedPassage]]:
    """Embed bounded passages and return the best cosine window per text."""
    passages: list[str] = []
    owners: list[tuple[uuid.UUID, BoundedPassage]] = []
    for chunk_id, text in texts.items():
        for passage in bounded_token_passages(
            text,
            window_tokens=window_tokens,
            overlap_tokens=overlap_tokens,
            minimum_tokens=minimum_tokens,
        ):
            passages.append(passage.text)
            owners.append((chunk_id, passage))
    if not passages:
        return {}
    embedded = await embedder.embed_texts(passages, purpose=EmbeddingPurpose.DOCUMENT)
    best: dict[uuid.UUID, tuple[float, BoundedPassage]] = {}
    for (chunk_id, passage), vector in zip(owners, embedded.vectors, strict=True):
        score = cosine_similarity(query_vector, vector)
        current = best.get(chunk_id)
        if current is None or score > current[0]:
            best[chunk_id] = (score, passage)
    return best
