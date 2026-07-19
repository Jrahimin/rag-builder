"""Command dispatcher for ``python -m app.cli``."""

from __future__ import annotations

import argparse

from app.cli.doctor_cli import main as doctor_main


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("doctor", help="Check configuration and local dependencies")
    args = parser.parse_args()
    if args.command == "doctor":
        return doctor_main()
    return 2  # pragma: no cover - argparse rejects unknown commands


if __name__ == "__main__":
    raise SystemExit(main())
