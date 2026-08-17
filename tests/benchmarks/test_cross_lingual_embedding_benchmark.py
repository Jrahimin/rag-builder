"""Opt-in provider-backed cross-lingual dense-retrieval acceptance benchmark."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

import pytest

from app.core.config import EmbeddingBackend, get_settings
from app.platform.providers.implementations.embedding_factory import create_embedding_provider

pytestmark = [
    pytest.mark.skipif(
        os.getenv("APE_RUN_MULTILINGUAL_PROVIDER_EVAL", "false").lower() != "true",
        reason="Set APE_RUN_MULTILINGUAL_PROVIDER_EVAL=true with a real embedding provider",
    ),
    pytest.mark.integration,
    pytest.mark.benchmark,
    pytest.mark.asyncio,
]

_CORPUS_PATH = Path(__file__).with_name("cross_lingual_corpus.json")


async def test_cross_lingual_embedding_recall_at_one(record_property) -> None:
    settings = get_settings()
    assert settings.embedding.backend is not EmbeddingBackend.HASH
    payload = json.loads(_CORPUS_PATH.read_text(encoding="utf-8"))
    documents = payload["documents"]
    queries = payload["queries"]
    provider = create_embedding_provider(settings)
    texts = [row["text"] for row in documents] + [row["text"] for row in queries]
    vectors = (await provider.embed_texts(texts)).vectors
    document_vectors = vectors[: len(documents)]
    query_vectors = vectors[len(documents) :]

    successes = 0
    pair_results: dict[str, list[bool]] = {}
    positive_scores: list[float] = []
    hard_negative_scores: list[float] = []
    for query, query_vector in zip(queries, query_vectors, strict=True):
        scored = [
            (document, _cosine(query_vector, vector))
            for document, vector in zip(documents, document_vectors, strict=True)
        ]
        ranked = sorted(
            scored,
            key=lambda item: item[1],
            reverse=True,
        )
        matched = ranked[0][0]["id"] == query["relevant_id"]
        pair_results.setdefault(query["language_pair"], []).append(matched)
        successes += int(matched)
        positive_scores.extend(
            score for document, score in scored if document["id"] == query["relevant_id"]
        )
        hard_negative_scores.extend(
            score for document, score in scored if document["id"] != query["relevant_id"]
        )

    recall_at_one = successes / len(queries)
    record_property("cross_lingual_recall_at_1", recall_at_one)
    record_property(
        "language_pair_results",
        json.dumps(
            {pair: all(matched) for pair, matched in sorted(pair_results.items())},
            sort_keys=True,
        ),
    )
    record_property("positive_semantic_score_min", min(positive_scores))
    record_property("hard_negative_semantic_score_max", max(hard_negative_scores))
    assert recall_at_one >= settings.evaluation.minimum_cross_lingual_recall_at_k
    assert all(all(matched) for matched in pair_results.values())
    assert min(positive_scores) > max(hard_negative_scores)


def _cosine(left: list[float], right: list[float]) -> float:
    denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(
        sum(value * value for value in right)
    )
    return sum(a * b for a, b in zip(left, right, strict=True)) / denominator
