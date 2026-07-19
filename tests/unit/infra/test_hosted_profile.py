"""Failure-path tests for hosted release and destructive-operation guards."""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[3] / "infra" / "hosted" / "hostedctl.py"
SPEC = importlib.util.spec_from_file_location("hostedctl", MODULE_PATH)
assert SPEC and SPEC.loader
hostedctl = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hostedctl)


def _release_lines(image: str) -> str:
    values = ["APE_DEPLOYMENT_ID=customer-a", "APE_PUBLIC_HOST=ape.example.test"]
    values.extend(f"{name}={image}" for name in hostedctl.REQUIRED_IMAGES)
    return "\n".join(values) + "\n"


def test_release_rejects_mutable_image_tag(tmp_path: Path) -> None:
    release = tmp_path / "release.env"
    release.write_text(_release_lines("registry.example/ape:latest"), encoding="utf-8")
    with pytest.raises(hostedctl.HostedOperationError, match="immutable"):
        hostedctl.validate_release(release)


def test_release_accepts_non_placeholder_digest(tmp_path: Path) -> None:
    release = tmp_path / "release.env"
    release.write_text(_release_lines(f"registry.example/ape@sha256:{'a' * 64}"), encoding="utf-8")
    assert hostedctl.validate_release(release)["APE_DEPLOYMENT_ID"] == "customer-a"


def test_restore_requires_deployment_specific_confirmation() -> None:
    with pytest.raises(hostedctl.HostedOperationError, match="RESTORE:customer-a"):
        hostedctl.require_confirmation(
            supplied="RESTORE:wrong",
            action="RESTORE",
            deployment_id="customer-a",
        )


def test_upgrade_surfaces_migration_failure_with_rollback_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    snapshot = tmp_path / "backups" / "before-upgrade"
    monkeypatch.setattr(
        hostedctl,
        "validate_release",
        lambda *_args, **_kwargs: {
            "APE_DEPLOYMENT_ID": "customer-a",
            "APE_PUBLIC_HOST": "ape.example.test",
        },
    )
    monkeypatch.setattr(hostedctl, "backup", lambda *_args: snapshot)
    monkeypatch.setattr(hostedctl, "compose_args", lambda _release: ["docker", "compose"])

    def fail_migration(args: list[str], **_kwargs: object) -> None:
        if "migrate" in args:
            raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(hostedctl, "run", fail_migration)
    with pytest.raises(hostedctl.HostedOperationError, match=re.escape(str(snapshot))):
        hostedctl.upgrade(tmp_path / "release.env", None)


def test_restore_runs_data_restore_migration_start_and_readiness(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    hosted_root = tmp_path / "hosted"
    secrets = hosted_root / "secrets"
    backup_root = hosted_root / "backups"
    snapshot = backup_root / "snapshot"
    secrets.mkdir(parents=True)
    snapshot.mkdir(parents=True)
    (snapshot / "objects").mkdir()
    (snapshot / "postgres.dump").write_bytes(b"dump")
    dump_sha256 = hostedctl.sha256_file(snapshot / "postgres.dump")
    (snapshot / "manifest.json").write_text(
        json.dumps(
            {
                "deployment_id": "customer-a",
                "database": "ape",
                "bucket": "ape-artifacts",
                "integrity": {"postgres_sha256": dump_sha256, "objects": []},
            }
        ),
        encoding="utf-8",
    )
    (secrets / "runtime.env").write_text(
        "POSTGRES_USER=ape\nPOSTGRES_DB=ape\nMINIO_BUCKET=ape-artifacts\n",
        encoding="utf-8",
    )
    release_env = hosted_root / "release.env"
    release_env.write_text(
        "APE_DEPLOYMENT_ID=customer-a\nAPE_PUBLIC_HOST=ape.example.test\n"
        "APE_BACKUP_DIR=./backups\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(hostedctl, "HOSTED_ROOT", hosted_root)
    monkeypatch.setattr(hostedctl, "COMPOSE_FILE", hosted_root / "compose.yaml")
    monkeypatch.setattr(
        hostedctl,
        "validate_release",
        lambda *_args, **_kwargs: {
            "APE_DEPLOYMENT_ID": "customer-a",
            "APE_PUBLIC_HOST": "ape.example.test",
        },
    )
    commands: list[list[str]] = []

    def record(args: list[str], **_kwargs: object) -> None:
        commands.append(args)

    readiness: list[str] = []
    monkeypatch.setattr(hostedctl, "run", record)
    monkeypatch.setattr(hostedctl, "verify_ready", lambda host: readiness.append(host))

    hostedctl.restore(release_env, snapshot, "RESTORE:customer-a")

    assert any(command[-3:] == ["run", "--rm", "migrate"] for command in commands)
    assert any(command[-2:] == ["up", "-d"] for command in commands)
    assert readiness == ["ape.example.test"]


def test_rollback_uses_previous_release_and_exact_restore_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    previous = tmp_path / "release.previous.env"
    snapshot = tmp_path / "snapshot"
    monkeypatch.setattr(
        hostedctl,
        "validate_release",
        lambda *_args, **_kwargs: {"APE_DEPLOYMENT_ID": "customer-a"},
    )
    calls: list[tuple[Path, Path, str]] = []
    monkeypatch.setattr(
        hostedctl,
        "restore",
        lambda release, backup, confirm: calls.append((release, backup, confirm)),
    )

    hostedctl.rollback(previous, snapshot, "ROLLBACK:customer-a")

    assert calls == [(previous, snapshot, "RESTORE:customer-a")]


def test_backup_object_inventory_changes_when_content_is_tampered(tmp_path: Path) -> None:
    objects = tmp_path / "objects"
    objects.mkdir()
    item = objects / "document.bin"
    item.write_bytes(b"original")
    expected = hostedctl.object_inventory(objects)
    item.write_bytes(b"tampered")

    assert hostedctl.object_inventory(objects) != expected
