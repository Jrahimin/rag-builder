"""CLI adapter for the local ``tax_v1`` RAG journey."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from app.cli.rag_journey import (
    DEFAULT_ARTIFACT_ROOT,
    DEFAULT_FIXTURE,
    JourneyError,
    JourneyOptions,
    parse_config_assignment,
    run_journey,
)
from app.core.config import Settings, get_settings
from app.platform.providers.implementations.storage_factory import get_storage_provider


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli rag-journey",
        description="Run the local production-path tax_v1 RAG regression journey.",
    )
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Set one safe runtime/query-time ProjectAIConfig leaf (repeatable).",
    )
    parser.add_argument(
        "--compare",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Run one second variant changing exactly one safe query-time leaf.",
    )
    parser.add_argument(
        "--compare-translation",
        action="store_true",
        help=(
            "Reuse the same Project/index and run a second variant with "
            "behavior.translation_policy=disabled."
        ),
    )
    parser.add_argument(
        "--keep-project",
        action="store_true",
        help="Retain the generated Project for inspection instead of purging it.",
    )
    parser.add_argument(
        "--allow-nonlocal-database",
        action="store_true",
        help="Explicitly allow the configured non-loopback PostgreSQL host.",
    )
    parser.add_argument(
        "--allow-nonlocal-storage",
        action="store_true",
        help="Explicitly allow the configured non-loopback MinIO endpoint.",
    )
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE, help=argparse.SUPPRESS)
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=DEFAULT_ARTIFACT_ROOT,
        help=argparse.SUPPRESS,
    )
    return parser


def _options(args: argparse.Namespace, *, configured_job_backend: str) -> JourneyOptions:
    overrides: dict[str, object] = {}
    for raw in args.set:
        key, value = parse_config_assignment(raw)
        if key in overrides:
            raise JourneyError(f"Duplicate --set key {key!r}.")
        overrides[key] = value
    if args.compare_translation and args.compare:
        raise JourneyError("Use either --compare-translation or --compare, not both.")
    if len(args.compare) > 1:
        raise JourneyError("V1 accepts only one --compare assignment.")
    comparison = (
        ("behavior.translation_policy", "disabled")
        if args.compare_translation
        else (parse_config_assignment(args.compare[0]) if args.compare else None)
    )
    return JourneyOptions(
        fixture=args.fixture.resolve(),
        artifact_root=args.artifact_root.resolve(),
        overrides=overrides,
        comparison=comparison,
        compare_translation=args.compare_translation,
        keep_project=args.keep_project,
        allow_nonlocal_database=args.allow_nonlocal_database,
        allow_nonlocal_storage=args.allow_nonlocal_storage,
        configured_job_backend=configured_job_backend,
    )


def _line(message: str) -> None:
    sys.stdout.write(f"[rag-journey] {message}\n")
    sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    """Apply a process-local inline transport override and return a shell exit code."""
    args = _parser().parse_args(argv)
    previous_backend = os.environ.get("APE_JOBS__BACKEND")
    previous_dispatcher = os.environ.get("APE_JOBS__DISPATCHER_ENABLED")
    try:
        configured = Settings().jobs.backend.value
        options = _options(args, configured_job_backend=configured)
        os.environ["APE_JOBS__BACKEND"] = "inline"
        os.environ["APE_JOBS__DISPATCHER_ENABLED"] = "false"
        get_settings.cache_clear()
        get_storage_provider.cache_clear()
        result, artifact_dir = asyncio.run(run_journey(get_settings(), options, progress=_line))
        _line(f"status={result['status']} reports={artifact_dir}")
        if result.get("project_id") and options.keep_project:
            _line(f"kept Project ID: {result['project_id']}")
        return (
            0
            if result["status"] == "passed"
            and result["cleanup"]["status"]
            in {
                "succeeded",
                "skipped_keep_project",
            }
            else 1
        )
    except (JourneyError, ValueError) as exc:
        sys.stderr.write(f"[rag-journey] FAIL: {exc}\n")
        return 2
    finally:
        if previous_backend is None:
            os.environ.pop("APE_JOBS__BACKEND", None)
        else:
            os.environ["APE_JOBS__BACKEND"] = previous_backend
        if previous_dispatcher is None:
            os.environ.pop("APE_JOBS__DISPATCHER_ENABLED", None)
        else:
            os.environ["APE_JOBS__DISPATCHER_ENABLED"] = previous_dispatcher
        get_settings.cache_clear()
        get_storage_provider.cache_clear()
