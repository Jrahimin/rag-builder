"""Unit tests for multilingual sentence splitting."""

from __future__ import annotations

import pytest

from app.modules.knowledge.services.chunking.sentence_similarity_service import split_sentences

pytestmark = pytest.mark.unit


def test_bangla_sentence_split() -> None:
    sentences = split_sentences("প্রথম বাক্য। দ্বিতীয় বাক্য।")
    assert sentences == ["প্রথম বাক্য।", "দ্বিতীয় বাক্য।"]


def test_ellipsis_sentence_split() -> None:
    sentences = split_sentences("First line… Second line.")
    assert len(sentences) == 2


def test_mixed_terminators() -> None:
    sentences = split_sentences("English end. বাংলা শেষ। More text!")
    assert len(sentences) == 3
