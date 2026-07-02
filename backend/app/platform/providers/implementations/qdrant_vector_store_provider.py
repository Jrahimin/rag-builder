"""Qdrant vector store provider."""

from __future__ import annotations

import uuid

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qmodels

from app.core.config import QdrantConfig, VectorStoreConfig
from app.platform.providers.contracts.vector_store import (
    BaseVectorStoreProvider,
    VectorPoint,
    VectorSearchFilter,
    VectorSearchResult,
)
from app.platform.providers.errors import ProviderError


class QdrantVectorStoreProvider(BaseVectorStoreProvider):
    """Store and search vectors in a single Qdrant collection."""

    def __init__(self, *, qdrant: QdrantConfig, vector_store: VectorStoreConfig) -> None:
        self._collection = vector_store.collection_name
        self._client = AsyncQdrantClient(
            host=qdrant.host,
            port=qdrant.port,
            grpc_port=qdrant.grpc_port,
            prefer_grpc=qdrant.prefer_grpc,
            https=qdrant.https,
            api_key=qdrant.api_key,
            check_compatibility=False,
        )

    @property
    def provider_name(self) -> str:
        return "qdrant"

    async def ensure_collection(self, *, dimensions: int) -> None:
        exists = await self._client.collection_exists(self._collection)
        if exists:
            return
        try:
            await self._client.create_collection(
                collection_name=self._collection,
                vectors_config=qmodels.VectorParams(
                    size=dimensions,
                    distance=qmodels.Distance.COSINE,
                ),
            )
        except Exception as exc:
            msg = f"Failed to create Qdrant collection {self._collection!r}"
            raise ProviderError(msg, provider_name=self.provider_name) from exc

    async def upsert_points(self, points: list[VectorPoint]) -> None:
        if not points:
            return
        qpoints = [
            qmodels.PointStruct(
                id=point.point_id,
                vector=point.vector,
                payload=point.payload,
            )
            for point in points
        ]
        try:
            await self._client.upsert(collection_name=self._collection, points=qpoints)
        except Exception as exc:
            msg = "Qdrant upsert failed"
            raise ProviderError(msg, provider_name=self.provider_name) from exc

    async def delete_by_document(
        self,
        *,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> None:
        try:
            await self._client.delete(
                collection_name=self._collection,
                points_selector=qmodels.FilterSelector(
                    filter=qmodels.Filter(
                        must=[
                            qmodels.FieldCondition(
                                key="project_id",
                                match=qmodels.MatchValue(value=str(project_id)),
                            ),
                            qmodels.FieldCondition(
                                key="document_id",
                                match=qmodels.MatchValue(value=str(document_id)),
                            ),
                        ]
                    )
                ),
            )
        except Exception as exc:
            msg = "Qdrant delete failed"
            raise ProviderError(msg, provider_name=self.provider_name) from exc

    async def search(
        self,
        *,
        query_vector: list[float],
        top_k: int,
        filters: VectorSearchFilter,
        score_threshold: float | None = None,
    ) -> list[VectorSearchResult]:
        must: list[qmodels.Condition] = [
            qmodels.FieldCondition(
                key="project_id",
                match=qmodels.MatchValue(value=str(filters.project_id)),
            )
        ]
        if filters.document_id is not None:
            must.append(
                qmodels.FieldCondition(
                    key="document_id",
                    match=qmodels.MatchValue(value=str(filters.document_id)),
                )
            )
        if filters.embedding_set_version is not None:
            must.append(
                qmodels.FieldCondition(
                    key="embedding_set_version",
                    match=qmodels.MatchValue(value=filters.embedding_set_version),
                )
            )
        for key, value in filters.metadata.items():
            must.append(
                qmodels.FieldCondition(
                    key=key,
                    match=qmodels.MatchValue(value=value),
                )
            )

        try:
            response = await self._client.query_points(
                collection_name=self._collection,
                query=query_vector,
                limit=top_k,
                query_filter=qmodels.Filter(must=must),
                score_threshold=score_threshold,
                with_payload=True,
            )
        except Exception as exc:
            msg = "Qdrant search failed"
            raise ProviderError(msg, provider_name=self.provider_name) from exc

        return [
            VectorSearchResult(
                point_id=str(hit.id),
                score=float(hit.score),
                payload=dict(hit.payload or {}),
            )
            for hit in response.points
        ]

    async def close(self) -> None:
        await self._client.close()
