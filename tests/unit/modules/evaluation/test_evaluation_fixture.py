"""Validate the checked-in representative dataset contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.modules.evaluation.schemas.evaluation import EvaluationDatasetCreate

pytestmark = pytest.mark.unit


def test_phase4_fixture_covers_required_case_types() -> None:
    path = Path("tests/fixtures/evaluation/phase4_quality_v1.json")
    dataset = EvaluationDatasetCreate.model_validate(json.loads(path.read_text(encoding="utf-8")))
    assert {case.kind.value for case in dataset.cases} == {
        "exact_token",
        "paraphrase",
        "metadata_filter",
        "multilingual",
        "no_answer",
        "citation",
    }


def test_cross_lingual_fixture_covers_bidirectional_and_same_script_pairs() -> None:
    path = Path("tests/fixtures/evaluation/cross_lingual_quality_v2.json")
    dataset = EvaluationDatasetCreate.model_validate(json.loads(path.read_text(encoding="utf-8")))
    pairs = {
        (case.query_language, case.expected_evidence_language)
        for case in dataset.cases
        if case.query_language and case.expected_evidence_language
    }

    assert {"cross_lingual", "code_switched", "multilingual", "no_answer"} <= {
        case.kind.value for case in dataset.cases
    }
    assert {("en", "bn"), ("bn", "en"), ("en", "fr"), ("fr", "en")} <= pairs
    assert any(case.expected_no_answer for case in dataset.cases)


def test_ocr_gazette_fixture_uses_rechunk_stable_evidence_and_hard_negatives() -> None:
    path = Path("tests/fixtures/evaluation/ocr_gazette_grounding_v1.json")
    dataset = EvaluationDatasetCreate.model_validate(json.loads(path.read_text(encoding="utf-8")))
    keys = {case.key for case in dataset.cases}
    table_phrases = {
        "সঞ্চয়পত্র হইতে অর্জিত",
        "রপ্তানির বিপরীতে",
        "অধিগ্রহণ",
        "হস্তান্তর",
        "মোটরযান",
    }

    answerable = [case for case in dataset.cases if not case.expected_no_answer]
    table_cases = [case for case in answerable if case.key.startswith("gazette.table.")]
    assert all(case.relevant_evidence_phrases for case in answerable)
    assert all(table_phrases <= set(case.relevant_evidence_phrases) for case in table_cases)
    assert any(case.kind.value == "cross_lingual" for case in answerable)
    assert {
        "gazette.decoy.act12-section163",
        "gazette.decoy.section106-recipient-rate",
        "gazette.decoy.unrelated",
    } <= keys
    assert sum(case.expected_no_answer for case in dataset.cases) >= 3
    assert any(
        case.query_language == "en"
        and case.expected_evidence_language == "bn"
        and not case.expected_no_answer
        for case in dataset.cases
    )
