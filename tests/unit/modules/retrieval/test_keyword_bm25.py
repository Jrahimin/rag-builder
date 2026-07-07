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


def test_normalize_text_collapses_whitespace() -> None:
    assert normalize_text("  Hello   WORLD ") == "Hello WORLD"
    assert term_frequencies(tokenize("a a b")) == {"a": 2, "b": 1}
