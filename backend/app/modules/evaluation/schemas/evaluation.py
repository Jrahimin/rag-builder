"""Versioned dataset, run, and operator quality schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EvaluationCaseKind(StrEnum):
    EXACT_TOKEN = "exact_token"
    PARAPHRASE = "paraphrase"
    METADATA_FILTER = "metadata_filter"
    MULTILINGUAL = "multilingual"
    NO_ANSWER = "no_answer"
    CITATION = "citation"


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    kind: EvaluationCaseKind
    query: str = Field(min_length=1, max_length=4096)
    relevant_chunk_ids: list[uuid.UUID] = Field(default_factory=list)
    relevant_document_ids: list[uuid.UUID] = Field(default_factory=list)
    document_id: uuid.UUID | None = None
    metadata_filter: dict[str, str] = Field(default_factory=dict)
    expected_answer_tokens: list[str] = Field(default_factory=list)
    expected_no_answer: bool = False

    @model_validator(mode="after")
    def validate_expectation(self) -> EvaluationCase:
        if self.expected_no_answer:
            return self
        if not self.relevant_chunk_ids and not self.relevant_document_ids:
            msg = "Answerable cases require a relevant chunk or document id"
            raise ValueError(msg)
        return self


class EvaluationDatasetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=64)
    schema_version: int = Field(default=1, ge=1)
    description: str | None = Field(default=None, max_length=2000)
    cases: list[EvaluationCase] = Field(min_length=1)


class EvaluationDatasetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    version: str
    schema_version: int
    description: str | None
    dataset_hash: str
    cases: list[EvaluationCase]
    created_at: datetime


class EvaluationRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: uuid.UUID
    top_k: int | None = Field(default=None, ge=1, le=100)


class EvaluationRunResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    dataset_id: uuid.UUID
    job_id: uuid.UUID
    job_state: str
    top_k: int
    configuration_hash: str
    versions: dict[str, Any]
    metrics: dict[str, Any]
    case_results: list[dict[str, Any]]
    regressions: list[dict[str, Any]]
    failed_cases: list[dict[str, Any]]
    reranker_comparison: dict[str, Any]
    completed_at: datetime | None
    created_at: datetime


class QualitySummary(BaseModel):
    dataset: EvaluationDatasetResponse | None = None
    last_run: EvaluationRunResponse | None = None
    acceptance_thresholds: dict[str, float]
