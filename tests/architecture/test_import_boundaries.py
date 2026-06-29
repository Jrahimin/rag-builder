"""Architecture import boundary enforcement."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2] / "backend" / "app"

# Prefixes that are forbidden imports for each package (app.* subpackages).
_FORBIDDEN: dict[str, frozenset[str]] = {
    "core": frozenset(
        {"app.platform", "app.modules", "app.api", "app.dependencies", "app.composition"}
    ),
    "platform": frozenset({"app.modules", "app.api", "app.dependencies", "app.composition"}),
    "modules": frozenset({"app.dependencies", "app.api", "app.composition"}),
}

# api/ and dependencies/ are composition layers — they may import modules and platform.
_COMPOSITION_PACKAGES = frozenset({"api", "dependencies", "composition"})


def _python_files_under(relative: str) -> list[Path]:
    root = BACKEND_ROOT / relative
    if not root.exists():
        return []
    return [p for p in root.rglob("*.py") if p.name != "__init__.py" or p.parent != root]


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def _package_for(path: Path) -> str | None:
    try:
        rel = path.relative_to(BACKEND_ROOT)
    except ValueError:
        return None
    parts = rel.parts
    return parts[0] if parts else None


@pytest.mark.unit
@pytest.mark.parametrize("package", sorted(_FORBIDDEN))
def test_package_import_boundaries(package: str) -> None:
    violations: list[str] = []
    for py_file in _python_files_under(package):
        for imported in _imported_modules(py_file):
            for forbidden in _FORBIDDEN[package]:
                if imported == forbidden or imported.startswith(forbidden + "."):
                    rel = py_file.relative_to(BACKEND_ROOT)
                    violations.append(f"{rel}: forbidden import '{imported}'")
    assert not violations, "Import boundary violations:\n" + "\n".join(violations)


@pytest.mark.unit
def test_modules_do_not_import_each_other_internals() -> None:
    """Modules may not import another module's subpackages (services, repositories, ...)."""
    violations: list[str] = []
    modules_root = BACKEND_ROOT / "modules"
    if not modules_root.exists():
        return

    module_names = {
        p.name for p in modules_root.iterdir() if p.is_dir() and not p.name.startswith("_")
    }

    for py_file in modules_root.rglob("*.py"):
        rel_parts = py_file.relative_to(modules_root).parts
        if not rel_parts:
            continue
        source_module = rel_parts[0]
        for imported in _imported_modules(py_file):
            if not imported.startswith("app.modules."):
                continue
            target_parts = imported.removeprefix("app.modules.").split(".")
            if not target_parts:
                continue
            target_module = target_parts[0]
            if target_module != source_module and target_module in module_names:
                violations.append(
                    f"{py_file.relative_to(BACKEND_ROOT)}: cross-module import '{imported}'"
                )
    assert not violations, "Cross-module import violations:\n" + "\n".join(violations)
