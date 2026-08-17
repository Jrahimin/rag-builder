"""Recorded lexical-rescue comparison: raw coverage vs corpus-IDF weighting."""

from __future__ import annotations

import math

import pytest

from app.core.config import ChatConfig
from app.modules.conversations.grounding_service import _coverage, _significant_tokens

pytestmark = pytest.mark.unit

_TABLE = (
    "সারণী আয়ের উৎস উৎসে কর সংগ্রহের খাত সঞ্চয়পত্র হইতে অর্জিত মুনাফা "
    "সম্পত্তি অধিগ্রহণের ক্ষতিপূরণ রপ্তানির বিপরীতে প্রাপ্ত নগদ ভর্তুকি "
    "সম্পত্তি হস্তান্তর বাণিজ্যিকভাবে পরিচালিত মোটরযান"
)
_SECTION_106 = (
    "২০২৩ সনের ১২ নং আইনের ধারা ১০৬ সংশোধন কোম্পানি করদাতার ক্ষেত্রে "
    "১৫ শতাংশ অন্যান্য করদাতার ক্ষেত্রে ১০ শতাংশ কর্তন হার"
)
_HEADER = "বাংলাদেশ গেজেট অতিরিক্ত সংখ্যা আইন বিচার ও সংসদ বিষয়ক মন্ত্রণালয়"
_BN_TABLE_QUERY = "উৎসে কর সংগ্রহের খাত কি?"
_EN_NUMERAL_QUERY = "tell me about law section 12 sub section 106 update"


def _idf_weighted_coverage(
    query_tokens: set[str],
    evidence_tokens: set[str],
    corpus: list[set[str]],
) -> float:
    if not query_tokens:
        return 1.0
    document_count = len(corpus)
    weights = {}
    for token in query_tokens:
        document_frequency = _document_frequency(token, corpus)
        weights[token] = math.log(
            1 + (document_count - document_frequency + 0.5) / (document_frequency + 0.5)
        )
    denominator = sum(weights.values())
    if denominator <= 0:
        return 0.0
    numerator = sum(weights[token] for token in query_tokens if token in evidence_tokens)
    return numerator / denominator


def _document_frequency(token: str, corpus: list[set[str]]) -> int:
    return sum(token in document for document in corpus)


def test_raw_token_coverage_matches_idf_on_the_table_versus_section_106_decision() -> None:
    config = ChatConfig()
    table_tokens = _significant_tokens(_TABLE)
    section_tokens = _significant_tokens(_SECTION_106)
    query_tokens = _significant_tokens(_BN_TABLE_QUERY)
    corpus = [
        table_tokens,
        section_tokens,
        _significant_tokens(_HEADER),
        _significant_tokens(_EN_NUMERAL_QUERY),
    ]

    raw_table = _coverage(query_tokens, table_tokens)
    raw_section = _coverage(query_tokens, section_tokens)
    idf_table = _idf_weighted_coverage(query_tokens, table_tokens, corpus)
    idf_section = _idf_weighted_coverage(query_tokens, section_tokens, corpus)

    assert raw_table >= config.lexical_corroboration_coverage
    assert raw_section < config.lexical_corroboration_coverage
    assert idf_table >= config.lexical_corroboration_coverage
    assert idf_section < config.lexical_corroboration_coverage
    assert raw_table > raw_section
    assert idf_table > idf_section
