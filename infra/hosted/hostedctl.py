#!/usr/bin/env python3
"""Guarded operator commands for one dedicated hosted APE deployment."""

# ruff: noqa: RUF005, T201

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import ssl
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

HOSTED_ROOT = Path(__file__).resolve().parent
COMPOSE_FILE = HOSTED_ROOT / "compose.yaml"
REQUIRED_IMAGES = (
    "APE_BACKEND_IMAGE",
    "APE_FRONTEND_IMAGE",
    "POSTGRES_IMAGE",
    "REDIS_IMAGE",
    "MINIO_IMAGE",
    "MC_IMAGE",
    "CLAMAV_IMAGE",
    "NGINX_IMAGE",
)
_DIGEST = re.compile(r"^.+@sha256:([0-9a-fA-F]{64})$")


class HostedOperationError(RuntimeError):
    pass


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        raise HostedOperationError(f"Missing environment file: {path}")
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise HostedOperationError(f"Invalid environment line in {path}: {raw_line}")
        name, value = line.split("=", 1)
        values[name.strip()] = value.strip()
    return values


def validate_release(release_env: Path, runtime_env: Path | None = None) -> dict[str, str]:
    release = read_env(release_env)
    for key in ("APE_DEPLOYMENT_ID", "APE_PUBLIC_HOST", *REQUIRED_IMAGES):
        if not release.get(key):
            raise HostedOperationError(f"{key} is required in {release_env}")
    for key in REQUIRED_IMAGES:
        match = _DIGEST.fullmatch(release[key])
        if match is None or set(match.group(1)) == {"0"}:
            raise HostedOperationError(
                f"{key} must be an approved immutable image@sha256 digest, not a tag/placeholder"
            )
    if runtime_env is not None:
        runtime = read_env(runtime_env)
        required_runtime = {
            "APE_APP__ENV": "production",
            "APE_AUTH__ENABLED": "true",
            "APE_WEBHOOKS__ENABLED": "true",
            "APE_WEBHOOKS__DISPATCHER_ENABLED": "true",
            "APE_STORAGE__BACKEND": "minio",
            "APE_MALWARE_SCAN__BACKEND": "clamav",
            "APE_JOBS__BACKEND": "taskiq",
            "APE_RETRIEVAL__STRATEGY": "hybrid",
        }
        for key, expected in required_runtime.items():
            if runtime.get(key, "").lower() != expected:
                raise HostedOperationError(f"{key} must be {expected!r} in {runtime_env}")
        for secret in (
            "APE_AUTH__ADMIN_API_KEY",
            "APE_AUTH__KEY_PEPPER",
            "APE_WEBHOOKS__SIGNING_KEY",
            "APE_DATABASE__PASSWORD",
            "APE_REDIS__PASSWORD",
            "APE_MINIO__SECRET_KEY",
        ):
            value = runtime.get(secret, "")
            if len(value.encode()) < 32 or value.startswith("replace-"):
                raise HostedOperationError(
                    f"{secret} must be a non-placeholder secret of 32+ bytes"
                )
    return release


def require_confirmation(*, supplied: str, action: str, deployment_id: str) -> None:
    expected = f"{action}:{deployment_id}"
    if supplied != expected:
        raise HostedOperationError(f"Refusing operation; pass --confirm {expected}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def object_inventory(root: Path) -> list[dict[str, str | int]]:
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "size": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(item for item in root.rglob("*") if item.is_file())
    ]


def compose_args(release_env: Path) -> list[str]:
    return [
        "docker",
        "compose",
        "--env-file",
        str(release_env),
        "-f",
        str(COMPOSE_FILE),
    ]


def run(args: list[str], *, stdout: object | None = None, stdin: object | None = None) -> None:
    subprocess.run(args, cwd=HOSTED_ROOT, check=True, stdout=stdout, stdin=stdin)


def backup(release_env: Path, destination: Path | None = None) -> Path:
    release = validate_release(release_env, HOSTED_ROOT / "secrets" / "runtime.env")
    base = Path(release.get("APE_BACKUP_DIR", "./backups"))
    if not base.is_absolute():
        base = (HOSTED_ROOT / base).resolve()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = (destination or (base / stamp)).resolve()
    try:
        relative_target = target.relative_to(base).as_posix()
    except ValueError as exc:
        raise HostedOperationError(
            f"Backup output must be inside configured APE_BACKUP_DIR: {base}"
        ) from exc
    target.mkdir(parents=True, exist_ok=False)
    runtime = read_env(HOSTED_ROOT / "secrets" / "runtime.env")
    database = runtime["POSTGRES_DB"]
    bucket = runtime["MINIO_BUCKET"]
    args = compose_args(release_env)
    run(args + ["stop", "gateway", "backend", "worker"])
    try:
        pg_dump = target / "postgres.dump"
        with pg_dump.open("wb") as output:
            run(
                args
                + [
                    "exec", "-T", "postgres", "pg_dump", "-U",
                    runtime["POSTGRES_USER"], "-d", database, "-Fc",
                ],
                stdout=output,
            )
        run(
            args
            + [
                "--profile",
                "ops",
                "run",
                "--rm",
                "--entrypoint",
                "/bin/sh",
                "mc",
                "-ec",
                "mc alias set local http://minio:9000 "
                '"$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"; '
                f'mc mirror --overwrite local/"{bucket}" '
                f"/backup/{relative_target}/objects",
            ]
        )
    finally:
        run(args + ["up", "-d", "backend", "worker", "gateway"])
    manifest = {
        "schema": 1,
        "deployment_id": release["APE_DEPLOYMENT_ID"],
        "created_at": datetime.now(UTC).isoformat(),
        "database": database,
        "bucket": bucket,
        "images": {name: release[name] for name in REQUIRED_IMAGES},
        "integrity": {
            "postgres_sha256": sha256_file(target / "postgres.dump"),
            "objects": object_inventory(target / "objects"),
        },
    }
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return target


def restore(release_env: Path, backup_dir: Path, confirmation: str) -> None:
    release = validate_release(release_env, HOSTED_ROOT / "secrets" / "runtime.env")
    require_confirmation(
        supplied=confirmation,
        action="RESTORE",
        deployment_id=release["APE_DEPLOYMENT_ID"],
    )
    backup_dir = backup_dir.resolve()
    manifest_path = backup_dir / "manifest.json"
    dump_path = backup_dir / "postgres.dump"
    if (
        not manifest_path.is_file()
        or not dump_path.is_file()
        or not (backup_dir / "objects").is_dir()
    ):
        raise HostedOperationError(
            "Backup is incomplete; manifest, postgres.dump, and objects are required"
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("deployment_id") != release["APE_DEPLOYMENT_ID"]:
        raise HostedOperationError("Backup deployment_id does not match this dedicated instance")
    runtime = read_env(HOSTED_ROOT / "secrets" / "runtime.env")
    if manifest.get("database") != runtime["POSTGRES_DB"]:
        raise HostedOperationError("Backup database does not match the configured target")
    if manifest.get("bucket") != runtime["MINIO_BUCKET"]:
        raise HostedOperationError("Backup object bucket does not match the configured target")
    integrity = manifest.get("integrity")
    if not isinstance(integrity, dict):
        raise HostedOperationError("Backup manifest has no integrity inventory")
    if integrity.get("postgres_sha256") != sha256_file(dump_path):
        raise HostedOperationError("Backup PostgreSQL dump checksum does not match")
    expected_objects = integrity.get("objects")
    actual_objects = object_inventory(backup_dir / "objects")
    if expected_objects != actual_objects:
        raise HostedOperationError("Backup object inventory/checksum does not match")
    args = compose_args(release_env)
    run(args + ["stop", "gateway", "frontend", "backend", "worker"])
    run(args + ["up", "-d", "postgres", "minio"])
    with dump_path.open("rb") as source:
        run(
            args
            + [
                "exec",
                "-T",
                "postgres",
                "pg_restore",
                "--clean",
                "--if-exists",
                "--no-owner",
                "-U",
                runtime["POSTGRES_USER"],
                "-d",
                runtime["POSTGRES_DB"],
            ],
            stdin=source,
        )
    release_values = read_env(release_env)
    base = Path(release_values.get("APE_BACKUP_DIR", "./backups"))
    if not base.is_absolute():
        base = (HOSTED_ROOT / base).resolve()
    relative_backup = backup_dir.relative_to(base).as_posix()
    run(
        args
        + [
            "--profile",
            "ops",
            "run",
            "--rm",
            "--entrypoint",
            "/bin/sh",
            "mc",
            "-ec",
            "mc alias set local http://minio:9000 "
            '"$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"; '
            f"mc mirror --overwrite --remove /backup/{relative_backup}/objects "
            'local/"$MINIO_BUCKET"',
        ]
    )
    run(args + ["run", "--rm", "migrate"])
    run(args + ["up", "-d"])
    verify_ready(release["APE_PUBLIC_HOST"])


def verify_ready(
    host: str,
    *,
    allow_untrusted_tls: bool = False,
    timeout_seconds: float = 120,
    poll_seconds: float = 5,
) -> None:
    context = (
        ssl._create_unverified_context()
        if allow_untrusted_tls
        else ssl.create_default_context()
    )
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"https://{host}/ready", timeout=10, context=context
            ) as response:
                if response.status == 200:
                    return
                last_error = HostedOperationError(
                    f"Readiness returned HTTP {response.status}"
                )
        except OSError as exc:
            last_error = exc
        time.sleep(poll_seconds)
    raise HostedOperationError(
        f"Readiness did not succeed within {timeout_seconds:g}s: {last_error}"
    )


def diagnostics(release_env: Path, output: Path) -> Path:
    release = validate_release(release_env)
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    args = compose_args(release_env)
    with (output / "compose-ps.txt").open("wb") as stream:
        run(args + ["ps", "--all"], stdout=stream)
    with (output / "service-logs.txt").open("wb") as stream:
        run(
            args + ["logs", "--no-color", "--tail", "500", "backend", "worker", "gateway"],
            stdout=stream,
        )
    with (output / "migration-heads.txt").open("wb") as stream:
        run(args + ["run", "--rm", "migrate", "alembic", "heads"], stdout=stream)
    summary = {
        "deployment_id": release["APE_DEPLOYMENT_ID"],
        "public_host": release["APE_PUBLIC_HOST"],
        "images": {name: release[name] for name in REQUIRED_IMAGES},
        "generated_at": datetime.now(UTC).isoformat(),
        "contains_secrets": False,
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    shutil.make_archive(str(output), "zip", output)
    return output.with_suffix(".zip")


def upgrade(release_env: Path, backup_destination: Path | None) -> None:
    release = validate_release(release_env, HOSTED_ROOT / "secrets" / "runtime.env")
    snapshot = backup(release_env, backup_destination)
    args = compose_args(release_env)
    run(args + ["pull"])
    try:
        run(args + ["run", "--rm", "migrate"])
        run(args + ["up", "-d"])
        verify_ready(release["APE_PUBLIC_HOST"])
    except Exception as exc:
        raise HostedOperationError(
            f"Upgrade failed. Preserve services and run rollback with snapshot {snapshot}: {exc}"
        ) from exc


def rollback(previous_release_env: Path, backup_dir: Path, confirmation: str) -> None:
    release = validate_release(previous_release_env, HOSTED_ROOT / "secrets" / "runtime.env")
    require_confirmation(
        supplied=confirmation,
        action="ROLLBACK",
        deployment_id=release["APE_DEPLOYMENT_ID"],
    )
    restore(
        previous_release_env,
        backup_dir,
        f"RESTORE:{release['APE_DEPLOYMENT_ID']}",
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--release-env", type=Path, default=HOSTED_ROOT / "release.env")
    sub = result.add_subparsers(dest="command", required=True)
    sub.add_parser("validate")
    backup_parser = sub.add_parser("backup")
    backup_parser.add_argument("--output", type=Path)
    restore_parser = sub.add_parser("restore")
    restore_parser.add_argument("backup", type=Path)
    restore_parser.add_argument("--confirm", required=True)
    upgrade_parser = sub.add_parser("upgrade")
    upgrade_parser.add_argument("--backup-output", type=Path)
    rollback_parser = sub.add_parser("rollback")
    rollback_parser.add_argument("backup", type=Path)
    rollback_parser.add_argument("--previous-release-env", type=Path, required=True)
    rollback_parser.add_argument("--confirm", required=True)
    diagnostic_parser = sub.add_parser("diagnostics")
    diagnostic_parser.add_argument("output", type=Path)
    return result


def main() -> int:
    arguments = parser().parse_args()
    try:
        if arguments.command == "validate":
            validate_release(arguments.release_env, HOSTED_ROOT / "secrets" / "runtime.env")
        elif arguments.command == "backup":
            print(backup(arguments.release_env, arguments.output))
        elif arguments.command == "restore":
            restore(arguments.release_env, arguments.backup, arguments.confirm)
        elif arguments.command == "upgrade":
            upgrade(arguments.release_env, arguments.backup_output)
        elif arguments.command == "rollback":
            rollback(arguments.previous_release_env, arguments.backup, arguments.confirm)
        elif arguments.command == "diagnostics":
            print(diagnostics(arguments.release_env, arguments.output))
    except (HostedOperationError, subprocess.CalledProcessError, OSError, ValueError) as exc:
        print(f"hostedctl: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
