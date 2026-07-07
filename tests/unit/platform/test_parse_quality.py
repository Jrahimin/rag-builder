"""Unit tests for parse quality assessment."""

from __future__ import annotations

import pytest

from app.platform.domain.parse_quality import (
    ExtractionMethod,
    PageExtractionRecord,
    PageExtractionStatus,
    ParseQualityScorer,
    summarize_page_extractions,
)

pytestmark = pytest.mark.unit


def test_scorer_accepts_normal_unicode_text() -> None:
    scorer = ParseQualityScorer(min_page_quality_score=0.55, min_text_chars=20)
    assessment = scorer.assess("Budget allocation for fiscal year 2026-27 increased by 12 percent.")
    assert assessment.is_acceptable
    assert assessment.score >= 0.55


def test_scorer_rejects_control_heavy_garbled_text() -> None:
    scorer = ParseQualityScorer(min_page_quality_score=0.55, min_text_chars=20)
    garbled = "\x01\x02\x03" * 40 + "abc"
    assessment = scorer.assess(garbled)
    assert not assessment.is_acceptable
    assert assessment.score < 0.55


def test_scorer_rejects_replacement_characters() -> None:
    scorer = ParseQualityScorer(min_page_quality_score=0.55, min_text_chars=20)
    assessment = scorer.assess("Budget \ufffd\ufffd\ufffd speech for citizens")
    assert not assessment.is_acceptable


def test_scorer_accepts_bangla_unicode_text() -> None:
    scorer = ParseQualityScorer(min_page_quality_score=0.55, min_text_chars=10)
    assessment = scorer.assess("বাজেট বক্তৃতা ২০২৬-২৭ অর্থনৈতিক প্রবৃদ্ধির লক্ষ্য নির্ধারণ করে।")
    assert assessment.is_acceptable


def test_scorer_rejects_custom_font_latin_garbled_text() -> None:
    scorer = ParseQualityScorer(min_page_quality_score=0.55, min_text_chars=20)
    garbled = (
        "rtolffitsft <l(qtfi-t q-?mFr qrqT flqq Fr6 r+<ffiEt<t mula-<fiut, ulot "
        "www.nbr.eov.bd Rtt: qht+T {R'E \\ot\\-\\o\\e cnIr.lufuq0xr.Fn s o6ftqqEn16a1qFrfi "
        'cfrrrt fi <t q"{Bd fuT <| fu dqf{ €fd 6q6 gqr{ qdqfd q-qfrs -{frd.t I qd qttq eo\\5 '
        'q< {tirt eo q( $$TT qErs-d q{i\'fm,t sbbs qr {r$ 53C q(r.ffi{ TTt c-st q(r"tlfiE '
        '{m frm E rqf"F €{l Rr{l: "53C. Collection of tax on sale price of goods or property '
        "sold by public auction.- Any person making sale, by public auction through sealed "
        "tender or otherwise, of any goods or property belonging to the Government shall "
        "collect advance tax at the rate of 10% (ten percent) of the sale price."
    )
    assessment = scorer.assess(garbled)
    assert not assessment.is_acceptable
    assert assessment.score < 0.55
    assert assessment.signals.unnatural_word_ratio > 0.05
    assert assessment.signals.segment_min_score < 0.55


def test_scorer_accepts_tariff_comparison_operators() -> None:
    scorer = ParseQualityScorer(min_page_quality_score=0.55, min_text_chars=20)
    assessment = scorer.assess(
        "Hot-Rolled Stainless Steel, In Coils, >=600mm By >10mm. "
        "OTHER: With maximum take-off weight>250g but <7 kg."
    )
    assert assessment.is_acceptable
    assert assessment.signals.unnatural_word_ratio < 0.05


def test_document_summary_supports_partial_success() -> None:
    pages = (
        PageExtractionRecord(
            page_number=1,
            text="Good page with enough readable content for indexing.",
            status=PageExtractionStatus.ACCEPTED,
            parse_quality_score=0.9,
            accepted_parser="pymupdf",
            extraction_method=ExtractionMethod.NATIVE_TEXT,
            char_count=52,
        ),
        PageExtractionRecord(
            page_number=2,
            text="",
            status=PageExtractionStatus.UNRECOVERABLE,
            parse_quality_score=0.1,
            accepted_parser="none",
            extraction_method=ExtractionMethod.NATIVE_TEXT,
            char_count=0,
        ),
    )
    summary = summarize_page_extractions(pages, total_page_count=2)
    assert summary.accepted_page_count == 1
    assert summary.failed_page_count == 1
    assert summary.partial_extraction is True
    assert summary.success_ratio == 0.5
