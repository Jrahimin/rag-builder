"""Tests for immutable, secret-free durable configuration snapshots."""

from __future__ import annotations

import pytest

from app.core.config import ChunkingStrategy, EmbeddingBackend, Settings
from app.platform.jobs.configuration import apply_job_configuration, build_job_configuration

pytestmark = pytest.mark.unit


def test_job_configuration_hash_is_stable_and_excludes_secrets() -> None:
    settings = Settings(
        embedding={
            "backend": "openai",
            "model": "embed-v1",
            "openai_api_key": "never-persist-this",
        }
    )

    first = build_job_configuration(settings)
    second = build_job_configuration(settings)

    assert first.digest() == second.digest()
    assert "never-persist-this" not in str(first.model_dump())
    assert "openai_api_key" not in first.index["embedding"]


def test_apply_job_configuration_restores_typed_values_and_live_secret() -> None:
    current = Settings(
        embedding={
            "backend": "openai",
            "model": "current",
            "openai_api_key": "live-secret",
        }
    )
    snapshot = build_job_configuration(
        current.model_copy(
            update={
                "chunking": current.chunking.model_copy(
                    update={"strategy": ChunkingStrategy.HEADING}
                ),
                "embedding": current.embedding.model_copy(update={"model": "snapshotted"}),
            }
        )
    )

    restored = apply_job_configuration(current, snapshot)

    assert restored.chunking.strategy is ChunkingStrategy.HEADING
    assert restored.embedding.backend is EmbeddingBackend.OPENAI
    assert restored.embedding.model == "snapshotted"
    assert restored.embedding.openai_api_key == "live-secret"
