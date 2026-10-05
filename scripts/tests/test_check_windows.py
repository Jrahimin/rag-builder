"""Offline tests of Make equivalence, fail-closed execution and dry-run safety."""

import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts/check_windows.py"
SPEC = importlib.util.spec_from_file_location("check_windows", SCRIPT)
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)


class WindowsQualityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name)
        (self.repo / "frontend").mkdir()
        (self.repo / "Makefile").write_text((REPO / "Makefile").read_text(), encoding="utf-8")
        self.package = json.loads((REPO / "frontend/package.json").read_text())
        self.write_package()
        (self.repo / "frontend/pnpm-lock.yaml").write_text("# fixture only\n")

    def write_package(self):
        (self.repo / "frontend/package.json").write_text(json.dumps(self.package))

    def call(self, *args):
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            return adapter.main(["--repo", str(self.repo), *args])

    def test_actual_quality_command_order_and_boundaries(self):
        self.assertEqual(
            adapter.verified_recipes(REPO, "quality"),
            [
                "$(PYTHON) -m ruff format --check backend tests scripts infra/hosted",
                "$(PYTHON) -m ruff check backend tests scripts infra/hosted",
                "$(PYTHON) -m mypy --no-incremental",
                "$(PYTHON) scripts/check_migrations.py",
                "$(PYTHON) -m pytest tests/unit tests/architecture",
                "$(PYTHON) -m pytest tests/integration",
                "$(PYTHON) -m pytest tests/evaluation -m evaluation_smoke",
                "cd frontend && $(PNPM) format:check",
                "cd frontend && $(PNPM) lint",
                "cd frontend && $(PNPM) typecheck",
                "cd frontend && $(PNPM) test",
            ],
        )
        for target in adapter.TARGETS:
            adapter.verified_recipes(REPO, target)

    def test_recipe_and_prerequisite_drift_block(self):
        baseline = (self.repo / "Makefile").read_text()
        changes = [
            baseline.replace("-m mypy --no-incremental", "-m mypy --ignore-missing-imports"),
            baseline.replace("quality: format-check", "quality: migrate-local format-check"),
            baseline.replace(
                "frontend-quality: frontend-format-check",
                "frontend-quality: frontend-build frontend-format-check",
            ),
        ]
        for text in changes:
            with self.subTest(text=text):
                (self.repo / "Makefile").write_text(text)
                with self.assertRaises(adapter.Blocked):
                    adapter.verified_recipes(self.repo, "quality")

    def test_unknown_and_duplicate_targets_block(self):
        with self.assertRaises(adapter.Blocked):
            adapter.verified_recipes(REPO, "migrate-local")
        with self.assertRaises(adapter.Blocked):
            adapter.parse_make("lint:\n\tfirst\nlint:\n\tsecond\n")

    def test_included_quality_prerequisite_cannot_be_silently_skipped(self):
        baseline = (self.repo / "Makefile").read_text()
        (self.repo / "quality-extra.mk").write_text("quality: new-required-check\n")
        for directive in ["include", "sinclude", "-include"]:
            with self.subTest(directive=directive):
                (self.repo / "Makefile").write_text(baseline + f"\n{directive} quality-extra.mk\n")
                with patch.object(
                    adapter.subprocess, "run", side_effect=AssertionError("invocation")
                ):
                    self.assertEqual(self.call("--target", "quality", "--dry-run"), 2)

    def test_conditional_override_and_eval_constructs_block(self):
        baseline = (self.repo / "Makefile").read_text()
        constructs = [
            "ifeq ($(OS),Windows_NT)\nquality: required\nendif\n",
            "override PYTHON = alternate-python\n",
            "define ADDED\nquality: required\nendef\n$(eval $(ADDED))\n",
            "unrelated: $(eval quality: required)\n",
            ".SECONDEXPANSION:\n",
            "export MAKEFLAGS = --ignore-errors\n",
        ]
        for construct in constructs:
            with self.subTest(construct=construct):
                (self.repo / "Makefile").write_text(baseline + "\n" + construct)
                with patch.object(
                    adapter.subprocess, "run", side_effect=AssertionError("invocation")
                ):
                    self.assertEqual(self.call("--target", "quality", "--dry-run"), 2)

    def test_dry_run_invokes_nothing_and_does_not_require_db_consent(self):
        with (
            patch.object(adapter.subprocess, "run", side_effect=AssertionError("invocation")),
            patch.dict(os.environ, {}, clear=True),
        ):
            self.assertEqual(self.call("--target", "quality", "--dry-run"), 0)

    def test_quality_consent_blocks_before_any_subprocess(self):
        with patch.object(adapter.subprocess, "run", side_effect=AssertionError("invocation")):
            self.assertEqual(self.call("--target", "quality"), 2)

    def test_disposable_explicit_settings_not_local_flag(self):
        env = {
            "APE_APP__ENV": "testing",
            "APE_DATABASE__HOST": "127.0.0.1",
            "APE_REDIS__HOST": "localhost",
            "APE_DATABASE__NAME": "fixture_test",
            "APE_TEST_DATABASE__NAME": "fixture_test",
            "APE_TEST_DATABASE__ALLOW_MIGRATIONS": "true",
        }
        adapter.check_test_environment("quality", True, env)
        for bad in [
            {},
            {**env, "APE_APP__ENV": "LOCAL"},
            {**env, "APE_DATABASE__HOST": "production.example"},
            {**env, "APE_TEST_DATABASE__NAME": "other_test"},
            {**env, "APE_TEST_DATABASE__ALLOW_MIGRATIONS": "false"},
        ]:
            with self.subTest(env=bad), self.assertRaises(adapter.Blocked):
                adapter.check_test_environment("quality", True, bad)
        with self.assertRaises(adapter.Blocked):
            adapter.check_test_environment("migration-drift-check", False, env)

    def test_frontend_package_and_script_drift_block_without_invocation(self):
        recipes = adapter.verified_recipes(self.repo, "frontend-quality")
        adapter.check_frontend(self.repo, recipes)
        self.package["packageManager"] = "pnpm@11.0.0"
        self.write_package()
        with self.assertRaises(adapter.Blocked):
            adapter.check_frontend(self.repo, recipes)
        self.package["packageManager"] = "pnpm@11.22.0"
        self.package["scripts"]["test"] = "vitest run || exit 0"
        self.write_package()
        with self.assertRaises(adapter.Blocked):
            adapter.check_frontend(self.repo, recipes)

    def test_first_command_failure_stops_and_corepack_network_is_disabled(self):
        with (
            patch.object(adapter, "check_versions"),
            patch.object(
                adapter.subprocess, "run", return_value=SimpleNamespace(returncode=5)
            ) as run,
        ):
            self.assertEqual(self.call("--target", "test-unit"), 5)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.kwargs["env"]["COREPACK_ENABLE_NETWORK"], "0")
            self.assertEqual(run.call_args.kwargs["cwd"], self.repo)

    def test_alembic_check_is_separate_and_has_backend_cwd(self):
        recipes = adapter.verified_recipes(REPO, "migration-drift-check")
        commands, _ = adapter.tool_plan(REPO, recipes)
        self.assertEqual(commands[0][0], REPO / "backend")
        self.assertEqual(commands[0][1][-3:], ["-m", "alembic", "check"])
        self.assertNotIn("upgrade", commands[0][1])

    def test_corepack_pinned_fallback_has_no_install_command(self):
        with patch.object(
            adapter.shutil,
            "which",
            side_effect=lambda name: "corepack.cmd" if name.startswith("corepack") else None,
        ):
            commands, _ = adapter.tool_plan(REPO, adapter.verified_recipes(REPO, "frontend-build"))
        self.assertEqual(commands[0][1], ["corepack.cmd", "pnpm@11.22.0", "build"])


if __name__ == "__main__":
    unittest.main()
