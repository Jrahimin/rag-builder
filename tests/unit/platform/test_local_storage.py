"""Unit tests for local filesystem storage."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.platform.providers.implementations.local_storage import LocalFilesystemStorageProvider

pytestmark = pytest.mark.unit


async def test_delete_document_tree_removes_document_directory(tmp_path: Path) -> None:
    root = tmp_path / "storage"
    provider = LocalFilesystemStorageProvider(root)
    project_id = uuid.uuid4()
    document_id = uuid.uuid4()
    document_dir = root / str(project_id) / str(document_id)
    parsed_dir = document_dir / "parsed"
    parsed_dir.mkdir(parents=True)
    (document_dir / "sample.pdf").write_bytes(b"%PDF-1.7")
    (parsed_dir / "v1.txt").write_text("parsed", encoding="utf-8")

    await provider.delete_document_tree(project_id=project_id, document_id=document_id)

    assert not document_dir.exists()
    assert (root / str(project_id)).is_dir()
