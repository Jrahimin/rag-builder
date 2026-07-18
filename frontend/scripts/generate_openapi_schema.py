"""Generate the frontend contract from the FastAPI application OpenAPI schema."""

from __future__ import annotations

import json
import sys
from pathlib import Path

FRONTEND_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = FRONTEND_ROOT.parent / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.main import create_app  # noqa: E402


def main() -> None:
    schema_path = FRONTEND_ROOT / "openapi-schema.json"
    schema_path.write_text(
        json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
