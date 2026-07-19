# ruff: noqa: T201
"""Validate that the checked-in Alembic graph has exactly one reachable head."""

from __future__ import annotations

import sys
from pathlib import Path

from alembic.script import ScriptDirectory


def main() -> int:
    repository_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repository_root / "backend"))
    migration_root = repository_root / "backend" / "app" / "composition" / "migrations"
    script = ScriptDirectory(str(migration_root))
    heads = script.get_heads()
    list(script.walk_revisions())
    if len(heads) != 1:
        print(f"Migration graph must have exactly one head; found: {heads}")
        return 1
    print(f"Migration graph valid; head={heads[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
