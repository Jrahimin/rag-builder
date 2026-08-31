"""Query-language routing and protected-literal preservation."""

from __future__ import annotations

import pytest

from app.platform.domain.language_detection import (
    QUERY_PROFILE_LATIN_AMBIGUOUS,
    ROUTING_LANGUAGE_MIXED,
    ROUTING_LANGUAGE_UNKNOWN,
    detect_query_language_profile,
    extract_protected_literals,
    missing_protected_literals,
    select_translation_target,
)

pytestmark = pytest.mark.unit


def test_latin_only_query_is_ambiguous_not_english() -> None:
    profile = detect_query_language_profile("what are the source tax deduction areas?")
    assert profile.is_latin_ambiguous is True
    assert profile.exact_primary is None
    assert profile.profile == QUERY_PROFILE_LATIN_AMBIGUOUS


def test_bangla_query_has_exact_primary() -> None:
    profile = detect_query_language_profile("উৎসে কর সংগ্রহের খাত কি?")
    assert profile.exact_primary == "bn"
    assert profile.profile == "bn"


def test_mixed_query_does_not_hide_behind_english() -> None:
    profile = detect_query_language_profile("Refund policy উৎসে কর applies")
    assert profile.is_mixed is True
    assert profile.profile == ROUTING_LANGUAGE_MIXED
    assert profile.exact_primary is None


def test_latin_query_translates_to_bangla_not_english_when_both_exist() -> None:
    profile = detect_query_language_profile("source tax deduction areas")
    target = select_translation_target(profile, {"bn": 12, "en": 80, "mixed": 1})
    assert target == "bn"


def test_bangla_query_on_bangla_only_corpus_skips_translation() -> None:
    profile = detect_query_language_profile("উৎসে কর সংগ্রহের খাত কি?")
    assert select_translation_target(profile, {"bn": 20}) is None


def test_english_only_corpus_skips_latin_query_translation() -> None:
    profile = detect_query_language_profile("refund policy")
    assert select_translation_target(profile, {"en": 9}) is None


def test_banglish_query_gets_bounded_english_rewrite_with_original_fallback() -> None:
    profile = detect_query_language_profile(
        "Current niyome BDT 60,000 eligible investment er rebate koto?"
    )
    assert profile.is_romanized_or_codeswitched is True
    assert select_translation_target(profile, {"en": 9}) == "en"


def test_normal_english_query_does_not_get_an_english_rewrite() -> None:
    profile = detect_query_language_profile("What is the current investment rebate rate?")
    assert profile.is_romanized_or_codeswitched is False
    assert select_translation_target(profile, {"en": 9}) is None


def test_protected_literals_keep_sections_dates_and_quoted_codes() -> None:
    query = 'See section 12 / 106 dated 2024-07-01 at 15% under "TDS-01" and BDT 1,000'
    literals = extract_protected_literals(query)
    assert "12" in literals
    assert "106" in literals
    assert "2024-07-01" in literals
    assert any("%" in item for item in literals)
    assert any("TDS-01" in item for item in literals)


def test_entity_name_drift_is_allowed_but_section_numbers_are_not() -> None:
    original = "Income Tax Ordinance section 163"
    translated = "আয়কর অধ্যাদেশ section 163"
    assert "163" not in missing_protected_literals(original, translated)
    dropped = missing_protected_literals(original, "আয়কর অধ্যাদেশ")
    assert "163" in dropped


def test_unicode_digits_may_translate_to_ascii_without_losing_numeric_meaning() -> None:
    original = "বর্তমান নিয়মে ৬০,০০০ টাকা বিনিয়োগ"  # noqa: RUF001
    translated = "current rule for 60,000 taka investment"
    assert missing_protected_literals(original, translated) == ()
    assert missing_protected_literals(original, "current rule for 60000 taka investment") == ()


def test_true_abbreviations_remain_exact_even_when_digits_normalize() -> None:
    original = "TDS ৬০,০০০"  # noqa: RUF001
    missing = missing_protected_literals(original, "60,000 tax deducted at source")
    assert "TDS" in missing


def test_empty_query_is_unknown() -> None:
    profile = detect_query_language_profile("12345")
    assert profile.profile == ROUTING_LANGUAGE_UNKNOWN
