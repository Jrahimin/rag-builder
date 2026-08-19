"""Unit tests for keyword tokenizer and BM25 scoring."""

from __future__ import annotations

import pytest

from app.modules.retrieval.keyword.bm25 import bm25_score
from app.modules.retrieval.keyword.tokenizer import normalize_text, term_frequencies, tokenize

pytestmark = pytest.mark.unit


def test_tokenize_extracts_alphanumeric_terms() -> None:
    assert tokenize("Refund Policy v2.0 — 30-day window!") == [
        "refund",
        "policy",
        "v2",
        "0",
        "30",
        "day",
        "window",
    ]


def test_tokenize_bangla_terms() -> None:
    tokens = tokenize("রিফান্ড নীতি ৩০ দিন")
    assert tokens
    assert "রিফান্ড" in tokens


def test_bm25_scores_matching_terms_higher() -> None:
    query = ["refund", "policy"]
    strong = bm25_score(
        query,
        term_frequencies={"refund": 2, "policy": 1},
        doc_length=10,
        avg_doc_length=10.0,
        total_documents=100,
        document_frequencies={"refund": 5, "policy": 8},
    )
    weak = bm25_score(
        query,
        term_frequencies={"refund": 0, "policy": 1},
        doc_length=10,
        avg_doc_length=10.0,
        total_documents=100,
        document_frequencies={"refund": 5, "policy": 8},
    )
    assert strong > weak > 0


def test_bm25_scores_a_present_term_when_collection_stats_are_missing() -> None:
    score = bm25_score(
        ["refund"],
        term_frequencies={"refund": 2},
        doc_length=10,
        avg_doc_length=10.0,
        total_documents=1,
        document_frequencies={},
    )
    assert score > 0


def test_english_query_tokens_do_not_match_bangla_term_frequencies() -> None:
    """English original-lexical 0 against a Bangla-only chunk is expected, not an indexer bug."""
    score = bm25_score(
        tokenize("source tax deduction", for_query=True),
        term_frequencies=term_frequencies(tokenize("উৎসে কর সংগ্রহের খাত সঞ্চয়পত্র")),
        doc_length=5,
        avg_doc_length=5.0,
        total_documents=1,
        document_frequencies={"উৎসে": 1, "কর": 1},
    )
    assert score == 0.0


def test_bangla_query_scores_when_collection_df_is_missing() -> None:
    query_terms = tokenize("উৎসে কর", for_query=True)
    score = bm25_score(
        query_terms,
        term_frequencies=term_frequencies(tokenize("উৎসে কর সংগ্রহের খাত সঞ্চয়পত্র")),
        doc_length=5,
        avg_doc_length=5.0,
        total_documents=1,
        document_frequencies={},
    )
    assert score > 0


def test_normalize_text_collapses_whitespace() -> None:
    assert normalize_text("  Hello   WORLD ") == "Hello WORLD"
    assert term_frequencies(tokenize("a a b")) == {"a": 2, "b": 1}
