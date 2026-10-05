# ruff: noqa: T201
"""Run verified Make quality equivalents with installed tools; never install dependencies."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Literal recipes are intentional: Make is authoritative. Changed recipes or
# prerequisites require reviewing this adapter, not interpreting shell code.
RECIPES = {
    "format-check": ((), ("$(PYTHON) -m ruff format --check backend tests scripts infra/hosted",)),
    "lint": ((), ("$(PYTHON) -m ruff check backend tests scripts infra/hosted",)),
    "typecheck": ((), ("$(PYTHON) -m mypy --no-incremental",)),
    "migration-check": ((), ("$(PYTHON) scripts/check_migrations.py",)),
    "migration-drift-check": ((), ("cd backend && $(PYTHON) -m alembic check",)),
    "test-unit": ((), ("$(PYTHON) -m pytest tests/unit tests/architecture",)),
    "test-integration": ((), ("$(PYTHON) -m pytest tests/integration",)),
    "eval-smoke": ((), ("$(PYTHON) -m pytest tests/evaluation -m evaluation_smoke",)),
    "frontend-format-check": ((), ("cd frontend && $(PNPM) format:check",)),
    "frontend-lint": ((), ("cd frontend && $(PNPM) lint",)),
    "frontend-typecheck": ((), ("cd frontend && $(PNPM) typecheck",)),
    "frontend-test": ((), ("cd frontend && $(PNPM) test",)),
    "frontend-build": ((), ("cd frontend && $(PNPM) build",)),
    "frontend-quality": (
        ("frontend-format-check", "frontend-lint", "frontend-typecheck", "frontend-test"),
        (),
    ),
    "quality": (
        (
            "format-check",
            "lint",
            "typecheck",
            "migration-check",
            "test-unit",
            "test-integration",
            "eval-smoke",
            "frontend-quality",
        ),
        (),
    ),
}
TARGETS = tuple(
    name
    for name in RECIPES
    if name not in {"frontend-format-check", "frontend-lint", "frontend-typecheck", "frontend-test"}
)
FRONTEND_SCRIPTS = {
    "format:check": "prettier --check .",
    "lint": "eslint . --max-warnings=0",
    "typecheck": "tsc -b --pretty false",
    "test": "vitest run",
    "build": "tsc -b && vite build",
}
LOOPBACK = {"localhost", "127.0.0.1", "::1"}


class Blocked(Exception):
    """Preflight failed; no quality command may execute."""


def parse_make(text: str) -> dict[str, tuple[tuple[str, ...], tuple[str, ...]]]:
    rules = {}
    current = None
    phony_continuation = False
    assignments = {"PYTHON ?= python", "PNPM ?= pnpm", "COMPOSE = docker compose"}
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if phony_continuation or line.startswith(".PHONY:"):
            names = line if phony_continuation else line.removeprefix(".PHONY:")
            phony_continuation = names.rstrip().endswith("\\")
            names = names.rstrip().removesuffix("\\")
            if any(not re.fullmatch(r"[\w-]+", name) for name in names.split()):
                raise Blocked(f"Unsupported .PHONY declaration at line {number}")
            current = None
        elif line.startswith("\t") and current:
            deps, commands = rules[current]
            rules[current] = (deps, (*commands, stripped))
        elif line.startswith("\t"):
            raise Blocked(f"Orphan Make recipe at line {number}")
        elif match := re.fullmatch(r"([\w-]+):\s*(.*)", line):
            current = match[1]
            if current in rules:
                raise Blocked(f"Duplicate Make target: {current}")
            dependencies = tuple(match[2].split())
            if any(not re.fullmatch(r"[\w-]+", name) for name in dependencies):
                raise Blocked(f"Unsupported Make prerequisite construct at line {number}")
            rules[current] = (dependencies, ())
        elif stripped in assignments:
            current = None
        else:
            # Fail closed on includes, conditionals, define/eval, overrides,
            # special rules or unknown assignments. Do not become a Make parser.
            raise Blocked(f"Unsupported Make construct at line {number}; review adapter")
    if phony_continuation:
        raise Blocked("Unterminated .PHONY continuation")
    return rules


def verified_recipes(repo: Path, target: str) -> list[str]:
    observed = parse_make((repo / "Makefile").read_text(encoding="utf-8"))
    result = []

    def visit(name: str) -> None:
        if name not in RECIPES or observed.get(name) != RECIPES[name]:
            raise Blocked(f"Make target drift or unsupported recipe: {name}; review adapter")
        dependencies, commands = RECIPES[name]
        for dependency in dependencies:
            visit(dependency)
        result.extend(commands)

    visit(target)
    return result


def check_test_environment(target: str, allow: bool, env: dict[str, str]) -> None:
    if target not in {"quality", "test-integration", "migration-drift-check"}:
        return
    if not allow:
        raise Blocked(
            "Requires --allow-integration acknowledging a verified disposable local test DB"
        )
    checks = {
        "APE_APP__ENV": env.get("APE_APP__ENV") == "testing",
        "APE_DATABASE__HOST": env.get("APE_DATABASE__HOST", "").lower() in LOOPBACK,
        "APE_REDIS__HOST": env.get("APE_REDIS__HOST", "").lower() in LOOPBACK,
        "test database": bool(env.get("APE_TEST_DATABASE__NAME"))
        and env.get("APE_DATABASE__NAME") == env.get("APE_TEST_DATABASE__NAME")
        and env.get("APE_TEST_DATABASE__NAME", "").endswith("_test"),
    }
    if target != "migration-drift-check":
        checks["APE_TEST_DATABASE__ALLOW_MIGRATIONS"] = (
            env.get("APE_TEST_DATABASE__ALLOW_MIGRATIONS", "").lower() == "true"
        )
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise Blocked("Missing explicit isolated test settings: " + ", ".join(failed))


def tool_plan(repo: Path, recipes: list[str]) -> tuple[list[tuple[Path, list[str]]], dict]:
    windows = os.name == "nt"
    python = repo / "backend" / "venv" / ("Scripts/python.exe" if windows else "bin/python")
    pnpm = shutil.which("pnpm.cmd" if windows else "pnpm")
    corepack = shutil.which("corepack.cmd" if windows else "corepack")
    pnpm_args = [pnpm] if pnpm else [corepack or "corepack", "pnpm@11.22.0"]
    plan = []
    for recipe in recipes:
        cwd = repo
        if recipe.startswith("cd backend && "):
            cwd, recipe = repo / "backend", recipe.removeprefix("cd backend && ")
        elif recipe.startswith("cd frontend && "):
            cwd, recipe = repo / "frontend", recipe.removeprefix("cd frontend && ")
        if recipe.startswith("$(PYTHON) "):
            argv = [str(python), *recipe.removeprefix("$(PYTHON) ").split()]
        else:
            argv = [*pnpm_args, *recipe.removeprefix("$(PNPM) ").split()]
        plan.append((cwd, argv))
    return plan, {"python": str(python), "pnpm": pnpm_args}


def check_frontend(repo: Path, recipes: list[str]) -> None:
    if not any("$(PNPM)" in recipe for recipe in recipes):
        return
    package = json.loads((repo / "frontend/package.json").read_text(encoding="utf-8"))
    if package.get("packageManager") != "pnpm@11.22.0":
        raise Blocked("Frontend packageManager drift; review CI and adapter")
    for recipe in recipes:
        if "$(PNPM)" in recipe:
            script = recipe.split("$(PNPM) ", 1)[1]
            if package.get("scripts", {}).get(script) != FRONTEND_SCRIPTS.get(script):
                raise Blocked(f"Frontend script drift: {script}")
    if not (repo / "frontend/pnpm-lock.yaml").is_file():
        raise Blocked("Missing frozen frontend lockfile")


def check_versions(tools: dict, recipes: list[str], env: dict[str, str]) -> None:
    probes = []
    if any("$(PYTHON)" in recipe for recipe in recipes):
        if not Path(tools["python"]).is_file():
            raise Blocked("Missing backend/venv Python; install separately with authorization")
        probes.append(([tools["python"], "--version"], "Python", None))
    if any("$(PNPM)" in recipe for recipe in recipes):
        node = shutil.which("node.exe" if os.name == "nt" else "node")
        if not node:
            raise Blocked("Missing Node 24")
        probes.extend(
            [
                ([node, "--version"], "Node", "24"),
                ([*tools["pnpm"], "--version"], "pnpm", "11.22.0"),
            ]
        )
    for argv, label, expected in probes:
        try:
            completed = subprocess.run(argv, env=env, capture_output=True, text=True, check=False)
        except OSError as error:
            raise Blocked(f"Missing {label} executable") from error
        value = completed.stdout.strip()
        if completed.returncode:
            raise Blocked(f"{label} version probe failed; no tool download attempted")
        if label == "Python":
            match = re.search(r"Python (\d+)\.(\d+)", value)
            valid = bool(match and (int(match[1]), int(match[2])) >= (3, 12))
        elif label == "Node":
            valid = value.lstrip("v").split(".")[0] == expected
        else:
            valid = value == expected
        if not valid:
            raise Blocked(
                f"{label} differs from required version; use Python >=3.12, Node 24, pnpm 11.22.0"
            )
        print(json.dumps({"tool": label, "version": value}))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--target", choices=TARGETS, default="quality")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--allow-integration",
        action="store_true",
        help="Acknowledge verified disposable local test DB; integration fixtures may migrate it",
    )
    options = parser.parse_args(argv)
    try:
        repo = options.repo.resolve(strict=True)
        recipes = verified_recipes(repo, options.target)
        check_frontend(repo, recipes)
        plan, tools = tool_plan(repo, recipes)
        print(
            json.dumps(
                {
                    "target": options.target,
                    "expected": (
                        "Python >=3.12 (CI 3.12); frontend Node 24, pnpm 11.22.0; "
                        "installed frozen dependencies"
                    ),
                }
            )
        )
        for cwd, command in plan:
            print(json.dumps({"cwd": str(cwd), "argv": command}))
        if options.dry_run:
            print("DRY RUN: no tools, DB, network, migrations or Docker invoked")
            return 0
        check_test_environment(options.target, options.allow_integration, dict(os.environ))
        env = dict(os.environ)
        env["COREPACK_ENABLE_NETWORK"] = "0"
        env["COREPACK_ENABLE_DOWNLOAD_PROMPT"] = "0"
        check_versions(tools, recipes, env)
        for cwd, command in plan:
            result = subprocess.run(command, cwd=cwd, env=env, check=False)
            print(json.dumps({"argv": command, "returncode": result.returncode}))
            if result.returncode:
                print("FAILED: stopped at first failed Make-equivalent command")
                return result.returncode if result.returncode > 0 else 1
        print("PASSED: requested local target; remote CI/Docker acceptance not established")
        return 0
    except (Blocked, OSError, ValueError) as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
