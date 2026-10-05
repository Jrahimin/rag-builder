"""Immutable identity captured before application modules load, never reread at acceptance."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

EXECUTION_CONTRACT = "rag.execution.scope-and-acceptance.v2"


def capture_code_identity(root: Path) -> str:
    rows = [
        (p.relative_to(root).as_posix(), hashlib.sha256(p.read_bytes()).hexdigest())
        for p in sorted(root.rglob("*.py"))
    ]
    return hashlib.sha256(
        json.dumps(
            {"execution_contract": EXECUTION_CONTRACT, "files": rows},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


LOADED_RUNTIME_IDENTITY = capture_code_identity(Path(__file__).resolve().parents[2])


def runtime_code_fingerprint() -> str:
    return LOADED_RUNTIME_IDENTITY
