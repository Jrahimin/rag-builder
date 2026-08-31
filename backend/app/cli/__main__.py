"""Command dispatcher for ``python -m app.cli``."""

from __future__ import annotations

import argparse
import sys

from app.cli.doctor_cli import main as doctor_main
from app.cli.rag_journey_cli import main as rag_journey_main


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "rag-journey":
        return rag_journey_main(sys.argv[2:])
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("doctor", help="Check configuration and local dependencies")
    subparsers.add_parser("rag-journey", help="Run the local tax_v1 RAG journey")
    args = parser.parse_args()
    if args.command == "doctor":
        return doctor_main()
    return 2  # pragma: no cover - argparse rejects unknown commands


if __name__ == "__main__":
    raise SystemExit(main())
