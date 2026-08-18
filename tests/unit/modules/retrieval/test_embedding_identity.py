"""Embedding identity parsing for query search and rollback-safe retained builds."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.exceptions import ConflictError
from app.modules.retrieval.embedding_identity import (
    identity_from_manifest,
    identity_from_vector_rows,
)

pytestmark = pytest.mark.unit


def test_manifest_identity_requires_provider_model_dimensions_and_esv() -> None:
    build = SimpleNamespace(
        embedding_set_version=3,
        manifest={
            "embedding_provider": "cohere",
            "embedding_model": "embed-v4.0",
            "embedding_dimensions": 1024,
            "embedding_set_version": 3,
        },
    )
    identity = identity_from_manifest(build)
    assert identity is not None
    assert identity.provider == "cohere"
    assert identity.model == "embed-v4.0"
    assert identity.dimensions == 1024
    assert identity.embedding_set_version == 3


def test_incomplete_manifest_is_unlabeled() -> None:
    build = SimpleNamespace(
        embedding_set_version=2,
        manifest={"embedding_provider": "openai"},
    )
    assert identity_from_manifest(build) is None


def test_manifest_esv_mismatch_is_incompatible() -> None:
    build = SimpleNamespace(
        embedding_set_version=3,
        manifest={
            "embedding_provider": "cohere",
            "embedding_model": "embed-v4.0",
            "embedding_dimensions": 1024,
            "embedding_set_version": 2,
        },
    )
    with pytest.raises(ConflictError) as caught:
        identity_from_manifest(build)
    assert caught.value.code == "embedding_identity_incompatible"


def test_vector_rows_recover_unlabeled_retained_build() -> None:
    build = SimpleNamespace(embedding_set_version=2, manifest={})
    identity = identity_from_vector_rows(build, [(2, "openai", "text-embedding-3-large", 1024)])
    assert identity is not None
    assert identity.source == "vectors"
    assert identity.provider == "openai"


def test_mixed_vector_identities_are_incompatible() -> None:
    build = SimpleNamespace(embedding_set_version=2, manifest={})
    with pytest.raises(ConflictError) as caught:
        identity_from_vector_rows(
            build,
            [
                (2, "openai", "text-embedding-3-large", 1024),
                (2, "cohere", "embed-v4.0", 1024),
            ],
        )
    assert caught.value.code == "embedding_identity_incompatible"
