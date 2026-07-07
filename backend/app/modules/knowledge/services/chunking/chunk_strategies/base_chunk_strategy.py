"""Base chunk strategy contract."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.modules.knowledge.services.chunking.models import ChunkingContext, DraftChunk


class BaseChunkStrategy(ABC):
    """Chunk a parsed document using a specific strategy."""

    @abstractmethod
    def chunk(self, context: ChunkingContext) -> list[DraftChunk]:
        """Produce ordered draft chunks from the parsed document."""
