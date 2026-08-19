"""Background job foundation — minimal application-facing contract.

Worker handler registration and Taskiq wiring are introduced with the first real
background job. See ``docs/architecture/background-processing.md``.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, Field

type JobProgressCallback = Callable[[str, int], Awaitable[None]]

_INDEX_OUTPUT_EXCLUDED_RETRIEVAL_KEYS = frozenset(
    {
        "default_top_k",
        "score_threshold",
        "strategy",
        "semantic_candidate_top_k",
        "keyword_candidate_top_k",
        "hnsw_ef_search",
        "rrf_k",
        "semantic_weight",
        "keyword_weight",
        "rerank_enabled",
        "rerank_top_n",
        "rerank_candidate_window",
        "rerank_return_n",
        "rerank_score_threshold",
        "reranker_backend",
        "max_chunks_per_document",
        "max_chunks_per_section",
        "deduplicate_by_content_hash",
        "passage_scoring_enabled",
        "passage_window_tokens",
        "passage_overlap_tokens",
        "passage_min_tokens",
    }
)


class RetryPolicy(BaseModel):
    """Durable execution retry limit stored on :class:`JobRun`."""

    max_attempts: int = Field(default=3, ge=1)


class JobDefinition(BaseModel):
    """Description staged durably before a Taskiq delivery is attempted."""

    name: str
    project_id: uuid.UUID
    document_id: uuid.UUID | None = None
    payload_version: int = 1
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None
    retry: RetryPolicy = Field(default_factory=RetryPolicy)


class JobConfiguration(BaseModel):
    """Normalized, secret-free processing, indexing, and quality configuration."""

    schema_version: int = 3
    processing: dict[str, Any]
    index: dict[str, Any]
    quality: dict[str, Any]
    execution: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)

    def digest(self) -> str:
        """Hash the complete immutable snapshot, including observed provenance."""
        payload = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def output_digest(self) -> str:
        """Hash settings that can change output, excluding the observed active index.

        The active index pointer is captured so a job can explain the world it was
        staged in, but it is not an input to document embedding/indexing. Including
        it in idempotency keys makes activation itself spuriously create new work.
        Source metadata is joined at retrieval time and therefore cannot change
        processing, chunking, vector, or keyword index output.
        """
        payload = json.dumps(
            {
                "schema_version": self.schema_version,
                "processing": self.processing,
                "index": self.index,
                "quality": self.quality,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def index_output_digest(self) -> str:
        """Hash only settings that can change corpus index artifacts.

        Retrieval ranking defaults and Project chat/LLM policy are execution-time
        concerns. They must not make an otherwise identical vector/keyword corpus
        appear to be a different build.
        """
        retrieval = {
            key: value
            for key, value in self.index["retrieval"].items()
            if key not in _INDEX_OUTPUT_EXCLUDED_RETRIEVAL_KEYS
        }
        payload = json.dumps(
            {
                "schema_version": self.schema_version,
                "processing": self.processing,
                "index": {
                    "embedding": self.index["embedding"],
                    "retrieval": retrieval,
                },
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


class JobSubmission(BaseModel):
    """Identity returned by asynchronous product actions."""

    job_id: uuid.UUID
    created: bool


class JobQueue(ABC):
    """Executor transport contract used only by the durable outbox dispatcher."""

    @abstractmethod
    async def enqueue(self, job: JobDefinition) -> str:
        """Place a job on the queue. Returns the assigned job id."""


class DurableJobSubmitter(ABC):
    """Cross-module seam for staging and opportunistically dispatching jobs."""

    @abstractmethod
    async def stage(
        self,
        job: JobDefinition,
        configuration: JobConfiguration,
        *,
        configuration_snapshot_id: uuid.UUID | None = None,
        retry_of_job_id: uuid.UUID | None = None,
    ) -> JobSubmission:
        """Stage a job and outbox intent in the caller's transaction."""

    @abstractmethod
    async def dispatch(self, job_id: uuid.UUID) -> None:
        """Best-effort dispatch after the caller commits; never loses the intent."""
