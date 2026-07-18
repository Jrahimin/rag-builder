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
