"""Active-build embedding identity used for query embedding and diagnostics."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from app.core.exceptions import ConflictError
from app.platform.providers.contracts.embedding import BaseEmbeddingProvider


class _IndexBuildIdentity(Protocol):
    embedding_set_version: int
    manifest: Any


@dataclass(frozen=True, slots=True)
class EmbeddingIdentity:
    """Complete vector-space identity for one index build."""

    embedding_set_version: int
    provider: str
    model: str
    dimensions: int
    source: str

    def matches(self, embedder: BaseEmbeddingProvider) -> bool:
        return (
            embedder.provider_name == self.provider
            and embedder.model_name == self.model
            and embedder.dimensions == self.dimensions
        )


QueryEmbedderFactory = Callable[[EmbeddingIdentity], BaseEmbeddingProvider]


def identity_from_manifest(build: _IndexBuildIdentity) -> EmbeddingIdentity | None:
    """Return a complete identity from the sealed build manifest, or None if unlabeled."""
    manifest = build.manifest if isinstance(build.manifest, dict) else {}
    provider = _optional_name(manifest.get("embedding_provider"))
    model = _optional_name(manifest.get("embedding_model"))
    dimensions = _optional_positive_int(manifest.get("embedding_dimensions"))
    if provider is None or model is None or dimensions is None:
        return None
    manifest_esv = _optional_positive_int(manifest.get("embedding_set_version"))
    build_esv = int(build.embedding_set_version)
    if manifest_esv is not None and manifest_esv != build_esv:
        raise ConflictError(
            "The active index build embedding_set_version does not match its stored identity.",
            code="embedding_identity_incompatible",
            context={
                "build_embedding_set_version": build_esv,
                "manifest_embedding_set_version": manifest_esv,
                "provider": provider,
                "model": model,
                "dimensions": dimensions,
            },
        )
    return EmbeddingIdentity(
        embedding_set_version=build_esv,
        provider=provider,
        model=model,
        dimensions=dimensions,
        source="manifest",
    )


def identity_from_vector_rows(
    build: _IndexBuildIdentity,
    rows: Sequence[tuple[int, str, str, int]],
) -> EmbeddingIdentity | None:
    """Derive identity from stored vectors so unlabeled retained builds stay rollback-safe."""
    unique: set[tuple[int, str, str, int]] = set()
    for embedding_set_version, provider, model, dimensions in rows:
        name = _optional_name(provider)
        model_name = _optional_name(model)
        dims = _optional_positive_int(dimensions)
        esv = _optional_positive_int(embedding_set_version)
        if name is None or model_name is None or dims is None or esv is None:
            raise ConflictError(
                "The active index build stores an incomplete embedding identity.",
                code="embedding_identity_incompatible",
            )
        unique.add((esv, name, model_name, dims))
    if not unique:
        return None
    if len(unique) > 1:
        raise ConflictError(
            "The active index build mixes embedding providers, models, or dimensions.",
            code="embedding_identity_incompatible",
            context={"identities": sorted(unique)},
        )
    embedding_set_version, provider, model, dimensions = next(iter(unique))
    build_esv = int(build.embedding_set_version)
    if embedding_set_version != build_esv:
        raise ConflictError(
            "Stored vectors do not match the active index build embedding_set_version.",
            code="embedding_identity_incompatible",
            context={
                "build_embedding_set_version": build_esv,
                "vector_embedding_set_version": embedding_set_version,
                "provider": provider,
                "model": model,
                "dimensions": dimensions,
            },
        )
    return EmbeddingIdentity(
        embedding_set_version=build_esv,
        provider=provider,
        model=model,
        dimensions=dimensions,
        source="vectors",
    )


def unlabeled_identity_error(*, index_build_id: object) -> ConflictError:
    return ConflictError(
        "The active index build has no complete embedding identity "
        "(embedding_set_version, provider, model, and dimensions). "
        "Rebuild and activate a new index; unlabeled or mixed vector spaces cannot be searched.",
        code="embedding_identity_unlabeled",
        context={"index_build_id": str(index_build_id)},
    )


def incompatible_identity_error(identity: EmbeddingIdentity) -> ConflictError:
    return ConflictError(
        "The query embedding provider does not match the active index build. "
        "Query embeddings follow the active build; rebuild, activate, or roll back "
        "instead of mixing vector spaces.",
        code="embedding_identity_incompatible",
        context={
            "embedding_set_version": identity.embedding_set_version,
            "provider": identity.provider,
            "model": identity.model,
            "dimensions": identity.dimensions,
            "source": identity.source,
        },
    )


def _optional_name(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _optional_positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = int(value)
    return number if number > 0 else None
