"""Embedding provider contract and result DTO."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum

from app.platform.providers.errors import ProviderError


class EmbeddingPurpose(StrEnum):
    """Vendor-neutral reason a vector is requested.

    Retrieval services pass QUERY or DOCUMENT. Provider implementations may
    map these onto vendor input types; they must not leak those names upward.
    """

    QUERY = "query"
    DOCUMENT = "document"


@dataclass(frozen=True, slots=True)
class EmbeddingBatchResult:
    """Normalized output from an embedding provider."""

    vectors: list[list[float]]
    provider: str
    model: str
    dimensions: int
    provider_version: str


def coerce_embedding_vector(
    values: object,
    *,
    dimensions: int,
    provider_name: str,
) -> list[float]:
    """Validate one raw embedding row (shape + dimensionality) and coerce to floats."""
    if not isinstance(values, list):
        msg = f"{provider_name} returned an invalid embedding row"
        raise ProviderError(msg, provider_name=provider_name)
    if len(values) != dimensions:
        msg = (
            f"{provider_name} embedding dimension mismatch: "
            f"expected {dimensions}, got {len(values)}"
        )
        raise ProviderError(msg, provider_name=provider_name)
    return [float(value) for value in values]


class BaseEmbeddingProvider(ABC):
    """Generate dense vectors for text inputs."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Stable provider identifier stored on chunk_embeddings."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Model identifier stored on chunk_embeddings."""

    @property
    @abstractmethod
    def dimensions(self) -> int:
        """Vector dimensionality."""

    @property
    @abstractmethod
    def provider_version(self) -> str:
        """Provider implementation version for audit."""

    @abstractmethod
    async def embed_texts(
        self,
        texts: list[str],
        *,
        purpose: EmbeddingPurpose = EmbeddingPurpose.DOCUMENT,
    ) -> EmbeddingBatchResult:
        """Embed a batch of texts into dense vectors."""
