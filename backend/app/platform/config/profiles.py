"""Code-owned configuration profiles and certification metadata.

Profile definitions are frozen value objects and hashes identify their exact content in snapshots.
The simple IDs are intentionally development-stage names; explicit public profile
versioning can be added when profiles become a persisted compatibility contract.
Certification stays separate so candidates can be exercised in Test Lab without
becoming normal Project recommendations.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from app.core.capability_profiles import (
    DEPLOYMENT_CAPABILITY_PROFILES,
    DeploymentCapabilityProfile,
    deployment_capability_profile,
)
from app.core.config import (
    EmbeddingBackend,
    EvidenceScoreMode,
    RerankerBackend,
    RerankMode,
    Settings,
)
from app.core.exceptions import BadRequestError
from app.core.generation_models import GENERATION_MODEL_REGISTRY
from app.core.runtime_validation import ProductionConfigurationError
from app.platform.domain.language_detection import LANGUAGE_METADATA_SCHEMA_VERSION

PROFILE_REGISTRY_VERSION = "2026-09-02"


class CertificationStatus(StrEnum):
    CANDIDATE = "candidate"
    CERTIFIED = "certified"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class ProfileCertification:
    profile_id: str
    status: CertificationStatus
    manifest_id: str | None = None
    evaluated_at: str | None = None
    evaluation_suites: tuple[str, ...] = ()
    notes: str = "Candidate awaiting all required evaluation suites."


@dataclass(frozen=True, slots=True)
class RAGExecutionProfile:
    id: str
    semantic_candidate_top_k: int
    keyword_candidate_top_k: int
    hnsw_ef_search: int
    rerank_mode: RerankMode
    rerank_candidate_window: int
    rerank_return_count: int
    retrieval_top_k: int
    max_context_chunks: int
    context_char_budget: int
    max_history_messages: int
    rrf_k: int = 60
    semantic_weight: float = 1.0
    keyword_weight: float = 1.0
    score_threshold: float = 0.0
    rerank_score_threshold: float = 0.0
    min_ocr_confidence: float = 0.0
    max_chunks_per_document: int = 4
    max_chunks_per_section: int = 2
    deduplicate_by_content_hash: bool = True
    passage_scoring_enabled: bool = False
    passage_window_tokens: int = 96
    passage_overlap_tokens: int = 24
    passage_min_tokens: int = 32
    max_related_sources: int = 8
    max_relationship_candidates: int = 20


@dataclass(frozen=True, slots=True)
class EvidenceCalibrationProfile:
    id: str
    embedding_provider: EmbeddingBackend
    embedding_model: str
    embedding_dimensions: int
    reranker_provider: RerankerBackend
    reranker_model: str
    score_method: EvidenceScoreMode
    semantic_threshold: float
    lexical_floor: float
    lexical_coverage: float
    cross_language_semantic_threshold: float
    minimum_reranker_score: float
    high_confidence_reranker_score: float
    minimum_claim_token_coverage: float
    minimum_claim_semantic_score: float


@dataclass(frozen=True, slots=True)
class IndexProfile:
    id: str
    parsing: Mapping[str, Any]
    ocr: Mapping[str, Any]
    chunking: Mapping[str, Any]
    embedding_provider: EmbeddingBackend
    embedding_model: str
    embedding_dimensions: int
    embedding_set_version: int
    fts_regconfig: str
    filterable_metadata_keys: tuple[str, ...]
    language_metadata_schema_version: str
    artifact_schema_version: int = 1


def _frozen_mapping(value: dict[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(value)


_COMMON_PARSING = _frozen_mapping(
    {
        "min_page_quality_score": 0.55,
        "min_document_success_ratio": 0.2,
        "min_text_chars": 20,
        "pdf_text_parsers": ("pymupdf", "pdfium"),
    }
)
_COMMON_OCR = _frozen_mapping(
    {
        "enabled": False,
        "backend": "noop",
        "bangla_backend": "noop",
        "lang": "en",
        "use_gpu": False,
        "bangla_min_ratio": 0.10,
        "max_ocr_pages_per_document": 100,
        "min_text_chars": 20,
        "min_image_area_ratio": 0.08,
        "dpi": 200,
        "min_page_confidence": 0.3,
    }
)
_HOSTED_OCR = _frozen_mapping(
    {
        **dict(_COMMON_OCR),
        "enabled": True,
        "backend": "google_vision",
        "bangla_backend": "google_vision",
    }
)
_COMMON_CHUNKING = _frozen_mapping(
    {
        "strategy": "auto",
        "target_tokens": 250,
        "max_tokens": 400,
        "min_tokens": 50,
        "structure_score_threshold": 0.55,
        "long_block_token_threshold": 600,
        "similarity_drop_threshold": 0.35,
        "chunker_version": "3.0.0",
        "token_count_method": "unicode_property_v1",
        "ocr_confidence_threshold": 0.5,
    }
)


RAG_EXECUTION_PROFILES: Mapping[str, RAGExecutionProfile] = MappingProxyType(
    {
        profile.id: profile
        for profile in (
            RAGExecutionProfile(
                id="economy",
                semantic_candidate_top_k=30,
                keyword_candidate_top_k=30,
                hnsw_ef_search=60,
                rerank_mode=RerankMode.ALWAYS,
                rerank_candidate_window=15,
                rerank_return_count=6,
                retrieval_top_k=8,
                max_context_chunks=6,
                context_char_budget=9_000,
                max_history_messages=12,
                max_chunks_per_document=3,
                max_chunks_per_section=1,
                passage_window_tokens=80,
                passage_overlap_tokens=16,
                passage_min_tokens=24,
                max_related_sources=4,
                max_relationship_candidates=10,
            ),
            RAGExecutionProfile(
                id="standard",
                semantic_candidate_top_k=50,
                keyword_candidate_top_k=50,
                hnsw_ef_search=100,
                rerank_mode=RerankMode.ALWAYS,
                rerank_candidate_window=25,
                rerank_return_count=8,
                retrieval_top_k=10,
                max_context_chunks=8,
                context_char_budget=12_000,
                max_history_messages=20,
            ),
            RAGExecutionProfile(
                id="quality",
                semantic_candidate_top_k=80,
                keyword_candidate_top_k=80,
                hnsw_ef_search=160,
                rerank_mode=RerankMode.ALWAYS,
                rerank_candidate_window=40,
                rerank_return_count=12,
                retrieval_top_k=12,
                max_context_chunks=10,
                context_char_budget=16_000,
                max_history_messages=20,
                max_chunks_per_document=6,
                max_chunks_per_section=3,
                passage_scoring_enabled=True,
                passage_window_tokens=128,
                passage_overlap_tokens=32,
                max_related_sources=8,
                max_relationship_candidates=20,
            ),
        )
    }
)


PROFILE_CERTIFICATIONS: Mapping[str, ProfileCertification] = MappingProxyType(
    {
        profile_id: ProfileCertification(
            profile_id=profile_id,
            status=CertificationStatus.CANDIDATE,
        )
        for profile_id in RAG_EXECUTION_PROFILES
    }
)


EVIDENCE_CALIBRATION_PROFILES: Mapping[str, EvidenceCalibrationProfile] = MappingProxyType(
    {
        profile.id: profile
        for profile in (
            EvidenceCalibrationProfile(
                id="hash-local-whole-chunk",
                embedding_provider=EmbeddingBackend.HASH,
                embedding_model="text-embedding-3-large",
                embedding_dimensions=1024,
                reranker_provider=RerankerBackend.NOOP,
                reranker_model="noop",
                score_method=EvidenceScoreMode.WHOLE_CHUNK,
                semantic_threshold=0.35,
                lexical_floor=0.30,
                lexical_coverage=0.50,
                cross_language_semantic_threshold=0.30,
                minimum_reranker_score=0.40,
                high_confidence_reranker_score=0.70,
                minimum_claim_token_coverage=0.35,
                minimum_claim_semantic_score=0.25,
            ),
            EvidenceCalibrationProfile(
                id="cohere-v4-managed-whole-chunk",
                embedding_provider=EmbeddingBackend.COHERE,
                embedding_model="embed-v4.0",
                embedding_dimensions=1024,
                reranker_provider=RerankerBackend.COHERE,
                reranker_model="rerank-v4.0-pro",
                score_method=EvidenceScoreMode.WHOLE_CHUNK,
                semantic_threshold=0.35,
                lexical_floor=0.30,
                lexical_coverage=0.50,
                cross_language_semantic_threshold=0.30,
                minimum_reranker_score=0.40,
                high_confidence_reranker_score=0.70,
                minimum_claim_token_coverage=0.35,
                minimum_claim_semantic_score=0.25,
            ),
            EvidenceCalibrationProfile(
                id="openai-large-cohere-whole-chunk",
                embedding_provider=EmbeddingBackend.OPENAI,
                embedding_model="text-embedding-3-large",
                embedding_dimensions=1024,
                reranker_provider=RerankerBackend.COHERE,
                reranker_model="rerank-v4.0-pro",
                score_method=EvidenceScoreMode.WHOLE_CHUNK,
                semantic_threshold=0.35,
                lexical_floor=0.30,
                lexical_coverage=0.50,
                cross_language_semantic_threshold=0.30,
                minimum_reranker_score=0.40,
                high_confidence_reranker_score=0.70,
                minimum_claim_token_coverage=0.35,
                minimum_claim_semantic_score=0.25,
            ),
            EvidenceCalibrationProfile(
                id="ollama-1024-local-whole-chunk",
                embedding_provider=EmbeddingBackend.OLLAMA,
                embedding_model="mxbai-embed-large",
                embedding_dimensions=1024,
                reranker_provider=RerankerBackend.LEXICAL,
                reranker_model="unicode-lexical-v1",
                score_method=EvidenceScoreMode.WHOLE_CHUNK,
                semantic_threshold=0.35,
                lexical_floor=0.30,
                lexical_coverage=0.50,
                cross_language_semantic_threshold=0.30,
                minimum_reranker_score=0.40,
                high_confidence_reranker_score=0.70,
                minimum_claim_token_coverage=0.35,
                minimum_claim_semantic_score=0.25,
            ),
        )
    }
)


INDEX_PROFILES: Mapping[str, IndexProfile] = MappingProxyType(
    {
        profile.id: profile
        for profile in (
            IndexProfile(
                id="development-hash",
                parsing=_COMMON_PARSING,
                ocr=_COMMON_OCR,
                chunking=_COMMON_CHUNKING,
                embedding_provider=EmbeddingBackend.HASH,
                embedding_model="text-embedding-3-large",
                embedding_dimensions=1024,
                embedding_set_version=2,
                fts_regconfig="simple",
                filterable_metadata_keys=("source", "tags", "ocr_confidence"),
                language_metadata_schema_version=LANGUAGE_METADATA_SCHEMA_VERSION,
            ),
            IndexProfile(
                id="hosted-cohere-v4",
                parsing=_COMMON_PARSING,
                ocr=_HOSTED_OCR,
                chunking=_COMMON_CHUNKING,
                embedding_provider=EmbeddingBackend.COHERE,
                embedding_model="embed-v4.0",
                embedding_dimensions=1024,
                embedding_set_version=3,
                fts_regconfig="simple",
                filterable_metadata_keys=("source", "tags", "ocr_confidence"),
                language_metadata_schema_version=LANGUAGE_METADATA_SCHEMA_VERSION,
            ),
            IndexProfile(
                id="hosted-openai-large",
                parsing=_COMMON_PARSING,
                ocr=_HOSTED_OCR,
                chunking=_COMMON_CHUNKING,
                embedding_provider=EmbeddingBackend.OPENAI,
                embedding_model="text-embedding-3-large",
                embedding_dimensions=1024,
                embedding_set_version=2,
                fts_regconfig="simple",
                filterable_metadata_keys=("source", "tags", "ocr_confidence"),
                language_metadata_schema_version=LANGUAGE_METADATA_SCHEMA_VERSION,
            ),
            IndexProfile(
                id="private-ollama-1024",
                parsing=_COMMON_PARSING,
                ocr=_COMMON_OCR,
                chunking=_COMMON_CHUNKING,
                embedding_provider=EmbeddingBackend.OLLAMA,
                embedding_model="mxbai-embed-large",
                embedding_dimensions=1024,
                embedding_set_version=2,
                fts_regconfig="simple",
                filterable_metadata_keys=("source", "tags", "ocr_confidence"),
                language_metadata_schema_version=LANGUAGE_METADATA_SCHEMA_VERSION,
            ),
        )
    }
)


def profile_hash(profile: object) -> str:
    payload = _json_value(profile)
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _json_value(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _json_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, StrEnum):
        return value.value
    return value


def deployment_profile(settings: Settings) -> DeploymentCapabilityProfile:
    return deployment_capability_profile(settings)


def execution_profile(profile_id: str, *, allow_candidate: bool = False) -> RAGExecutionProfile:
    try:
        profile = RAG_EXECUTION_PROFILES[profile_id]
    except KeyError as exc:
        raise BadRequestError(
            message="The RAG execution profile is not registered.",
            code="execution_profile_not_registered",
            context={"profile_id": profile_id},
        ) from exc
    certification = PROFILE_CERTIFICATIONS[profile_id]
    if certification.status is CertificationStatus.REJECTED and not allow_candidate:
        raise BadRequestError(
            message="The RAG execution profile has been rejected by evaluation.",
            code="execution_profile_rejected",
            context={"profile_id": profile_id, "status": certification.status.value},
        )
    return profile


def matching_execution_profile(
    values: Mapping[str, Any], *, certified_only: bool = False
) -> str | None:
    for profile_id, profile in RAG_EXECUTION_PROFILES.items():
        if (
            certified_only
            and PROFILE_CERTIFICATIONS[profile_id].status is not CertificationStatus.CERTIFIED
        ):
            continue
        comparable = execution_values(profile)
        if all(values.get(key) == value for key, value in comparable.items()):
            return profile_id
    return None


def execution_values(profile: RAGExecutionProfile) -> dict[str, Any]:
    return {
        "retrieval_top_k": profile.retrieval_top_k,
        "semantic_candidate_top_k": profile.semantic_candidate_top_k,
        "keyword_candidate_top_k": profile.keyword_candidate_top_k,
        "hnsw_ef_search": profile.hnsw_ef_search,
        "rrf_k": profile.rrf_k,
        "semantic_weight": profile.semantic_weight,
        "keyword_weight": profile.keyword_weight,
        "score_threshold": profile.score_threshold,
        "rerank_mode": profile.rerank_mode.value,
        "rerank_candidate_window": profile.rerank_candidate_window,
        "rerank_return_count": profile.rerank_return_count,
        "rerank_score_threshold": profile.rerank_score_threshold,
        "min_ocr_confidence": profile.min_ocr_confidence,
        "max_chunks_per_document": profile.max_chunks_per_document,
        "max_chunks_per_section": profile.max_chunks_per_section,
        "deduplicate_by_content_hash": profile.deduplicate_by_content_hash,
        "passage_scoring_enabled": profile.passage_scoring_enabled,
        "passage_window_tokens": profile.passage_window_tokens,
        "passage_overlap_tokens": profile.passage_overlap_tokens,
        "passage_min_tokens": profile.passage_min_tokens,
        "max_related_sources": profile.max_related_sources,
        "max_relationship_candidates": profile.max_relationship_candidates,
        "max_context_chunks": profile.max_context_chunks,
        "context_char_budget": profile.context_char_budget,
        "max_history_messages": profile.max_history_messages,
    }


def calibration_profile_for(settings: Settings) -> EvidenceCalibrationProfile:
    capability = deployment_profile(settings)
    return EVIDENCE_CALIBRATION_PROFILES[capability.calibration_profile_id]


def index_profile_for(settings: Settings) -> IndexProfile:
    capability = deployment_profile(settings)
    return INDEX_PROFILES[capability.default_index_profile_id]


def registry_errors() -> list[str]:
    errors: list[str] = []
    all_ids = [
        *DEPLOYMENT_CAPABILITY_PROFILES,
        *EVIDENCE_CALIBRATION_PROFILES,
        *RAG_EXECUTION_PROFILES,
        *INDEX_PROFILES,
    ]
    if len(all_ids) != len(set(all_ids)):
        errors.append("profile IDs must be globally unique")
    for profile in DEPLOYMENT_CAPABILITY_PROFILES.values():
        if profile.calibration_profile_id not in EVIDENCE_CALIBRATION_PROFILES:
            errors.append(f"{profile.id} references an unknown calibration profile")
        if profile.default_index_profile_id not in INDEX_PROFILES:
            errors.append(f"{profile.id} references an unknown index profile")
            continue
        calibration = EVIDENCE_CALIBRATION_PROFILES.get(profile.calibration_profile_id)
        index = INDEX_PROFILES[profile.default_index_profile_id]
        if calibration is not None and (
            calibration.embedding_provider is not index.embedding_provider
            or calibration.embedding_model != index.embedding_model
            or calibration.embedding_dimensions != index.embedding_dimensions
        ):
            errors.append(f"{profile.id} calibration and index embedding identities differ")
        if (
            calibration is not None
            and profile.reranker_provider is not None
            and calibration.reranker_provider is not profile.reranker_provider
        ):
            errors.append(f"{profile.id} calibration and capability rerankers differ")
        unknown_models = (
            set(profile.allowed_generation_model_ids) - GENERATION_MODEL_REGISTRY.keys()
        )
        if unknown_models:
            errors.append(
                f"{profile.id} references unknown generation models: "
                + ", ".join(sorted(unknown_models))
            )
        if profile.default_generation_model_id not in profile.allowed_generation_model_ids:
            errors.append(f"{profile.id} default generation model is not allowlisted")
    return errors


def compatibility_errors(settings: Settings) -> list[str]:
    capability = deployment_profile(settings)
    calibration = EVIDENCE_CALIBRATION_PROFILES[capability.calibration_profile_id]
    index = INDEX_PROFILES[capability.default_index_profile_id]
    errors = registry_errors()
    if settings.runtime.capability_profile_id is None:
        # Legacy runtime aliases remain readable for one compatibility window.
        return errors
    if capability.llm_provider is not None and settings.llm.backend is not capability.llm_provider:
        errors.append(f"{capability.id} requires llm backend {capability.llm_provider.value}")
    if (
        capability.web_search_provider is not None
        and settings.resolved_web_search_backend() is not capability.web_search_provider
    ):
        errors.append(
            f"{capability.id} requires web-search backend {capability.web_search_provider.value}"
        )
    if settings.embedding.backend is not index.embedding_provider:
        errors.append(
            f"{capability.id} requires embedding backend {index.embedding_provider.value}"
        )
    if settings.embedding.model != index.embedding_model:
        errors.append(f"{capability.id} requires embedding model {index.embedding_model}")
    if settings.embedding.dimensions != index.embedding_dimensions:
        errors.append(f"{capability.id} requires embedding dimensions {index.embedding_dimensions}")
    if settings.retrieval.embedding_set_version != index.embedding_set_version:
        errors.append(
            f"{capability.id} requires embedding set version {index.embedding_set_version}"
        )
    if settings.retrieval.reranker_backend is not calibration.reranker_provider:
        errors.append(
            f"{capability.id} requires reranker backend {calibration.reranker_provider.value}"
        )
    if (
        calibration.reranker_provider is RerankerBackend.COHERE
        and settings.reranker.cohere_model != calibration.reranker_model
    ):
        errors.append(f"{capability.id} requires reranker model {calibration.reranker_model}")
    actual_parsing = settings.parsing.model_dump(mode="json")
    actual_ocr = settings.ocr.model_dump(
        mode="json",
        exclude={
            "google_api_key",
            "google_endpoint",
            "google_timeout_seconds",
            "google_max_attempts",
        },
    )
    actual_chunking = settings.chunking.model_dump(
        mode="json",
        exclude={"semantic_batch_size"},
    )
    for name, actual, expected in (
        ("parsing", actual_parsing, _json_value(index.parsing)),
        ("OCR", actual_ocr, _json_value(index.ocr)),
        ("chunking", actual_chunking, _json_value(index.chunking)),
    ):
        if actual != expected:
            errors.append(f"{capability.id} {name} settings drift from index profile {index.id}")
    if settings.retrieval.fts_regconfig != index.fts_regconfig:
        errors.append(f"{capability.id} requires FTS regconfig {index.fts_regconfig}")
    if tuple(settings.retrieval.filterable_metadata_keys) != index.filterable_metadata_keys:
        errors.append(
            f"{capability.id} filterable metadata keys drift from index profile {index.id}"
        )
    if index.language_metadata_schema_version != LANGUAGE_METADATA_SCHEMA_VERSION:
        errors.append(f"{index.id} has stale language metadata schema identity")
    return errors


def validate_profile_compatibility(settings: Settings) -> None:
    """Fail startup when an explicit capability ID contradicts provider wiring."""
    errors = compatibility_errors(settings)
    if errors:
        raise ProductionConfigurationError(
            "Invalid deployment capability profile: " + "; ".join(errors)
        )
