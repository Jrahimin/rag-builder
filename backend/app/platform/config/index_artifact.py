"""Typed, allowlisted identity for materialized processing and index artifacts."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Settings
from app.platform.config.profiles import index_profile_for, profile_hash
from app.platform.domain.language_detection import LANGUAGE_METADATA_SCHEMA_VERSION

INDEX_ARTIFACT_FINGERPRINT_VERSION = 1


class RequiredIndexAction(StrEnum):
    NONE = "none"
    REPROCESS = "reprocess"
    REEMBED = "reembed"
    REINDEX = "reindex"
    REBUILD = "rebuild"


class IndexArtifactConfig(BaseModel):
    """Only fields capable of changing stored parse/chunk/vector/keyword output."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    fingerprint_version: int = Field(default=INDEX_ARTIFACT_FINGERPRINT_VERSION, frozen=True)
    index_profile_id: str | None = None
    index_profile_hash: str | None = None
    parsing: dict[str, Any]
    ocr: dict[str, Any]
    chunking: dict[str, Any]
    embedding: dict[str, Any]
    fts: dict[str, Any]
    materialized_metadata: dict[str, Any]

    def fingerprint_payload(self) -> dict[str, Any]:
        """Return materialized behavior only, excluding descriptive profile identity."""
        return self.model_dump(
            mode="json",
            exclude={"index_profile_id", "index_profile_hash"},
        )


class FutureProjectIndexSelection(BaseModel):
    """Reserved Super Admin contract; deliberately not persisted or exposed yet."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    index_profile_id: str | None = None
    target_artifact_fingerprint: str
    required_action: RequiredIndexAction


def build_index_artifact_config(settings: Settings) -> IndexArtifactConfig:
    """Construct the canonical artifact contract from deployment-owned settings."""
    target_profile = index_profile_for(settings)
    profile_backed = settings.runtime.capability_profile_id is not None
    return IndexArtifactConfig(
        index_profile_id=target_profile.id if profile_backed else None,
        index_profile_hash=profile_hash(target_profile) if profile_backed else None,
        parsing=settings.parsing.model_dump(mode="json"),
        ocr=settings.ocr.model_dump(
            mode="json",
            exclude={
                "google_api_key",
                "google_endpoint",
                "google_timeout_seconds",
                "google_max_attempts",
            },
        ),
        chunking=settings.chunking.model_dump(
            mode="json",
            exclude={"semantic_batch_size"},
        ),
        embedding={
            "backend": settings.embedding.backend.value,
            "model": settings.embedding.model,
            "dimensions": settings.embedding.dimensions,
        },
        fts={
            "regconfig": settings.retrieval.fts_regconfig,
        },
        materialized_metadata={
            "filterable_metadata_keys": settings.retrieval.filterable_metadata_keys,
            "language_metadata_schema_version": LANGUAGE_METADATA_SCHEMA_VERSION,
            "embedding_set_version": settings.retrieval.embedding_set_version,
        },
    )


def required_index_action(
    current: IndexArtifactConfig,
    target: IndexArtifactConfig,
) -> RequiredIndexAction:
    """Classify an explicit artifact change; equal contracts never schedule work."""
    if current.fingerprint_payload() == target.fingerprint_payload():
        return RequiredIndexAction.NONE
    if (
        current.parsing != target.parsing
        or current.ocr != target.ocr
        or current.chunking != target.chunking
    ):
        return RequiredIndexAction.REPROCESS
    if current.embedding != target.embedding:
        return RequiredIndexAction.REEMBED
    if current.fts != target.fts or current.materialized_metadata != target.materialized_metadata:
        return RequiredIndexAction.REINDEX
    return RequiredIndexAction.REBUILD
