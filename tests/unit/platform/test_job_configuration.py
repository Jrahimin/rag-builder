"""Tests for immutable, secret-free durable configuration snapshots."""

from __future__ import annotations

import pytest

from app.core.config import ChunkingStrategy, EmbeddingBackend, Settings
from app.platform.jobs.configuration import (
    apply_job_configuration,
    build_job_configuration,
    embedding_set_version_from_configuration,
)

pytestmark = pytest.mark.unit


def test_job_configuration_hash_is_stable_and_excludes_secrets() -> None:
    settings = Settings(
        embedding={
            "backend": "openai",
            "model": "embed-v1",
            "openai_api_key": "never-persist-this",
        },
        ocr={"google_api_key": "never-persist-google-key"},
        cohere={"api_key": "never-persist-cohere"},
        reranker={"cohere_api_key": "never-persist-legacy-cohere"},
    )

    first = build_job_configuration(settings)
    second = build_job_configuration(settings)

    assert first.digest() == second.digest()
    dumped = str(first.model_dump())
    assert "never-persist-this" not in dumped
    assert "openai_api_key" not in first.index["embedding"]
    assert "never-persist-google-key" not in dumped
    assert "google_api_key" not in first.processing["ocr"]
    assert "never-persist-cohere" not in dumped
    assert "never-persist-legacy-cohere" not in dumped
    assert "cohere_api_key" not in first.quality["reranker"]


def test_output_hash_ignores_observed_active_index_but_keeps_full_provenance() -> None:
    settings = Settings()
    first = build_job_configuration(
        settings,
        active_index_build_id="first",
        source_metadata_generation=1,
    )
    second = build_job_configuration(
        settings,
        active_index_build_id="second",
        source_metadata_generation=2,
    )

    assert first.digest() != second.digest()
    assert first.output_digest() == second.output_digest()


def test_apply_job_configuration_restores_typed_values_and_live_secret() -> None:
    current = Settings(
        embedding={
            "backend": "openai",
            "model": "current",
            "openai_api_key": "live-secret",
        },
        ocr={"google_api_key": "live-google-secret"},
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
    assert restored.ocr.google_api_key == "live-google-secret"


def test_apply_job_configuration_keeps_target_embedding_backend_for_new_builds() -> None:
    live = Settings(
        embedding={
            "backend": "cohere",
            "model": "embed-v4.0",
            "openai_api_key": "live-openai",
        },
        cohere={"api_key": "live-cohere"},
    )
    openai_snapshot = build_job_configuration(
        Settings(
            embedding={
                "backend": "openai",
                "model": "text-embedding-3-large",
                "openai_api_key": "ignored",
            }
        )
    )

    restored = apply_job_configuration(live, openai_snapshot)

    assert restored.embedding.backend is EmbeddingBackend.OPENAI
    assert restored.embedding.model == "text-embedding-3-large"
    assert restored.embedding.openai_api_key == "live-openai"
    assert restored.resolved_cohere_api_key() == "live-cohere"


def test_apply_job_configuration_preserves_embedding_set_version() -> None:
    snapshot = build_job_configuration(Settings(retrieval={"embedding_set_version": 3}))

    restored = apply_job_configuration(
        Settings(retrieval={"embedding_set_version": 2}),
        snapshot,
    )

    assert snapshot.index["retrieval"]["embedding_set_version"] == 3
    assert restored.retrieval.embedding_set_version == 3
    assert embedding_set_version_from_configuration(snapshot) == 3


def test_apply_job_configuration_keeps_live_esv_when_snapshot_omits_it() -> None:
    snapshot = build_job_configuration(Settings(retrieval={"embedding_set_version": 2}))
    retrieval = {
        key: value
        for key, value in snapshot.index["retrieval"].items()
        if key != "embedding_set_version"
    }
    incomplete = snapshot.model_copy(update={"index": {**snapshot.index, "retrieval": retrieval}})

    restored = apply_job_configuration(
        Settings(retrieval={"embedding_set_version": 3}),
        incomplete,
    )

    assert "embedding_set_version" not in incomplete.index["retrieval"]
    assert restored.retrieval.embedding_set_version == 3
