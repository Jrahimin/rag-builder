"""Validate/normalize a bounded local capture package without any network or DB action."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.modules.retrieval.acceptance_questions import REMEDIATION_ALIASES
from app.modules.retrieval.schemas.acceptance_report import ObservedAcceptanceReportCreate


def normalize_package(raw: bytes) -> ObservedAcceptanceReportCreate:
    if len(raw) > 8000000:
        raise ValueError("Local capture package exceeds eight MB")
    request = ObservedAcceptanceReportCreate.model_validate_json(raw)
    labels = {REMEDIATION_ALIASES.get(k, k): v for k, v in request.labels.items()}
    if len(labels) != len(request.labels):
        raise ValueError("Duplicate label aliases")
    return request.model_copy(
        update={
            "labels": labels,
            "turns": [
                v.model_copy(update={"case_id": REMEDIATION_ALIASES.get(v.case_id, v.case_id)})
                for v in request.turns
            ],
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    source = options.input.resolve(strict=True)
    target = options.output.resolve()
    if source == target or target.exists():
        parser.error("Output must be a new file distinct from the capture input")
    if source.stat().st_size > 8000000:
        parser.error("Capture input exceeds eight MB")
    request = normalize_package(source.read_bytes())
    target.write_text(request.model_dump_json(indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
