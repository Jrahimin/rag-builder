"""Base retriever contract."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.modules.retrieval.retrievers.models import CandidateHit, RetrievalContext


class BaseRetriever(ABC):
    """Project-scoped retriever returning candidate hits only."""

    @abstractmethod
    async def retrieve(self, context: RetrievalContext) -> list[CandidateHit]: ...
