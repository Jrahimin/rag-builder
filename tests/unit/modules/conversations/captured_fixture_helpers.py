"""Integrity-bound tracked captures; original local paths are provenance only."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

FIXTURE_ROOT = Path(__file__).parents[3] / "fixtures/evaluation"
CAPTURE_FIXTURE_SHA256 = {
    "message_journey_failures_20261001_v1.json": (
        "7c1813bf131917736d9e9a731626df9aa0dc9e32918776b99b3f9cc7ab65ae85"
    ),
    "phase2_recorded_q1_q4_timings_v1.json": (
        "0c495b51cd23275641d200a36d01e05e4869b0c0a119f14f62cacf133aafddee"
    ),
    "phase2_captured_budget_speech_v1.json": (
        "0623c310c3d98a8a0c92711667f16074ec480caa1c1808c6b94174abe36f8832"
    ),
}


def load_captured_fixture(name: str) -> dict[str, Any]:
    expected = CAPTURE_FIXTURE_SHA256[name]
    data = json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))
    canonical = json.dumps(
        data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    assert hashlib.sha256(canonical).hexdigest() == expected, f"Captured fixture changed: {name}"
    return data


def recorded_repair_for_case(case: dict[str, Any]) -> dict[str, Any]:
    baseline = load_captured_fixture("message_journey_failures_20261001_v1.json")
    matches = [row for row in baseline["cases"] if row["raw_path"] == case["source_file"]]
    assert len(matches) == 1, "Recorded timing must bind one captured production turn"
    captured = matches[0]
    assert captured["raw_sha256"] == case["source_file_sha256"].lower()
    assert captured["question"] == case["question"]
    return captured["repair"]
