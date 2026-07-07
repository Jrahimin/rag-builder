"""BM25 scoring utilities."""

from __future__ import annotations

import math


def bm25_score(
    query_terms: list[str],
    *,
    term_frequencies: dict[str, int],
    doc_length: int,
    avg_doc_length: float,
    total_documents: int,
    document_frequencies: dict[str, int],
    k1: float = 1.5,
    b: float = 0.75,
) -> float:
    """Compute Okapi BM25 relevance for one document against query terms."""
    if total_documents <= 0 or doc_length <= 0 or avg_doc_length <= 0:
        return 0.0

    score = 0.0
    for term in query_terms:
        tf = term_frequencies.get(term, 0)
        if tf == 0:
            continue
        df = document_frequencies.get(term, 0)
        if df == 0:
            continue
        idf = math.log(1 + (total_documents - df + 0.5) / (df + 0.5))
        numerator = tf * (k1 + 1)
        denominator = tf + k1 * (1 - b + b * (doc_length / avg_doc_length))
        score += idf * (numerator / denominator)
    return score
