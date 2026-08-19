"""Centralized, environment-driven application configuration.

All runtime configuration is loaded from environment variables (and an
optional ``.env`` file) via Pydantic Settings. Nothing AI-related or
infrastructure-related is hardcoded; every value is resolvable per
deployment and, later, per Project.

Environment variables use the ``APE_`` prefix and ``__`` as the nested
delimiter, e.g.::

    APE_APP__ENV=production
    APE_DATABASE__HOST=postgres
    APE_DATABASE__PASSWORD=secret

Access settings through :func:`get_settings`, which caches a single
``Settings`` instance for the process lifetime.
"""

from __future__ import annotations

import os
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy import URL


def _settings_env_files() -> tuple[str, ...] | None:
    """Load backend/.env even when the process cwd is the repository root."""
    if os.environ.get("APE_APP__ENV") == "testing":
        return None
    backend_env = Path(__file__).resolve().parents[2] / ".env"
    cwd_env = Path.cwd() / ".env"
    files: list[str] = []
    if cwd_env.is_file() and cwd_env.resolve() != backend_env.resolve():
        files.append(str(cwd_env))
    if backend_env.is_file():
        files.append(str(backend_env))
    return tuple(files) or (".env",)


class Environment(StrEnum):
    """Supported deployment environments."""

    DEVELOPMENT = "development"
    TESTING = "testing"
    PRODUCTION = "production"


class LogLevel(StrEnum):
    """Supported log levels."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class AppConfig(BaseModel):
    """Core application identity and runtime flags."""

    name: str = "AI Platform Engine"
    env: Environment = Environment.DEVELOPMENT
    debug: bool = True
    version: str = "0.9.0"
    api_v1_prefix: str = "/api/v1"

    @property
    def is_development(self) -> bool:
        return self.env is Environment.DEVELOPMENT

    @property
    def is_testing(self) -> bool:
        return self.env is Environment.TESTING

    @property
    def is_production(self) -> bool:
        return self.env is Environment.PRODUCTION


class ServerConfig(BaseModel):
    """Uvicorn / HTTP server configuration."""

    host: str = "0.0.0.0"
    port: int = 8000
    reload: bool = False
    workers: int = 1


class LoggingConfig(BaseModel):
    """Structured logging configuration."""

    level: LogLevel = LogLevel.INFO
    # Render logs as JSON (recommended for production / log aggregation).
    # When False, a human-friendly console renderer is used (local dev).
    render_json: bool = False


class RuntimeProfile(StrEnum):
    """Certified deployment capability profiles.

    ``hosted_managed`` is the preferred production profile (Cohere embed/rerank,
    OpenAI generation/translation). ``hosted_openai`` remains a deprecated
    compatibility profile that still certifies OpenAI embeddings so existing
    deploys do not suddenly require Cohere.
    """

    DEVELOPMENT = "development"
    HOSTED_MANAGED = "hosted_managed"
    HOSTED_OPENAI = "hosted_openai"
    PRIVATE_OLLAMA = "private_ollama"


class RuntimeConfig(BaseModel):
    """Production preflight and worker-observability settings."""

    # Preferred production value is hosted_managed. hosted_openai is compatibility-only.
    profile: RuntimeProfile = RuntimeProfile.DEVELOPMENT
    startup_timeout_seconds: float = Field(default=30.0, ge=1.0, le=300.0)
    dependency_timeout_seconds: float = Field(default=3.0, ge=0.1, le=30.0)
    provider_timeout_seconds: float = Field(default=15.0, ge=0.5, le=120.0)
    worker_heartbeat_seconds: int = Field(default=10, ge=1, le=300)
    worker_stale_seconds: int = Field(default=35, ge=3, le=900)

    @model_validator(mode="after")
    def _validate_worker_timing(self) -> RuntimeConfig:
        if self.worker_stale_seconds <= self.worker_heartbeat_seconds:
            msg = "runtime.worker_stale_seconds must exceed worker_heartbeat_seconds"
            raise ValueError(msg)
        return self


class CORSConfig(BaseModel):
    """Cross-Origin Resource Sharing configuration."""

    allow_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["*"])
    allow_credentials: bool = True
    allow_methods: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["*"])
    allow_headers: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["*"])

    @field_validator("allow_origins", "allow_methods", "allow_headers", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Allow comma-separated strings in addition to JSON lists from env."""
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped or stripped.startswith("["):
                return value
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return value


class DatabaseConfig(BaseModel):
    """PostgreSQL connection and async engine pool configuration."""

    host: str = "localhost"
    port: int = 5432
    user: str = "ape"
    password: str = "ape"
    name: str = "ape"

    echo: bool = False
    pool_size: int = 5
    max_overflow: int = 10
    pool_timeout: int = 30
    pool_recycle: int = 1800

    def _url(self, driver: str) -> str:
        return URL.create(
            drivername=driver,
            username=self.user,
            password=self.password,
            host=self.host,
            port=self.port,
            database=self.name,
        ).render_as_string(hide_password=False)

    @property
    def async_dsn(self) -> str:
        """Async SQLAlchemy DSN (asyncpg driver) used by the application."""
        return self._url("postgresql+asyncpg")

    @property
    def sync_dsn(self) -> str:
        """Synchronous DSN (psycopg) for tooling that requires it."""
        return self._url("postgresql+psycopg")


class DisposableDatabaseConfig(BaseModel):
    """Guards integration tests so they only migrate a disposable database."""

    name: str = "ape_test"
    allow_migrations: bool = False


class RedisConfig(BaseModel):
    """Redis connection configuration (cache + background queue backend)."""

    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: str | None = None

    @property
    def dsn(self) -> str:
        auth = f":{self.password}@" if self.password else ""
        return f"redis://{auth}{self.host}:{self.port}/{self.db}"


class MinioConfig(BaseModel):
    """MinIO / S3-compatible object storage connection configuration."""

    endpoint: str = "localhost:9000"
    access_key: str = "minioadmin"
    secret_key: str = "minioadmin"
    secure: bool = False
    region: str = "us-east-1"
    bucket: str = "ape-artifacts"

    @property
    def url(self) -> str:
        scheme = "https" if self.secure else "http"
        return f"{scheme}://{self.endpoint}"


class StorageBackend(StrEnum):
    """Supported object storage backends."""

    LOCAL = "local"
    MINIO = "minio"


class StorageConfig(BaseModel):
    """Object storage backend selection and local filesystem root."""

    backend: StorageBackend = StorageBackend.LOCAL
    local_root: str = "./storage"


class JobQueueBackend(StrEnum):
    """Supported background job queue backends."""

    TASKIQ = "taskiq"
    INLINE = "inline"


class JobsConfig(BaseModel):
    """Background job queue configuration."""

    backend: JobQueueBackend = JobQueueBackend.TASKIQ
    dispatcher_enabled: bool = True
    dispatcher_poll_seconds: float = Field(default=1.0, ge=0.1, le=60.0)
    dispatcher_batch_size: int = Field(default=50, ge=1, le=500)
    lease_seconds: int = Field(default=300, ge=10, le=3600)
    heartbeat_seconds: int = Field(default=30, ge=1, le=600)
    max_attempts: int = Field(default=3, ge=1, le=20)
    retry_base_delay_seconds: float = Field(default=2.0, ge=0.1, le=3600.0)
    retry_max_delay_seconds: float = Field(default=300.0, ge=1.0, le=86_400.0)
    dispatch_retry_base_seconds: float = Field(default=1.0, ge=0.1, le=3600.0)
    dispatch_retry_max_seconds: float = Field(default=60.0, ge=1.0, le=3600.0)

    @model_validator(mode="after")
    def _validate_heartbeat(self) -> JobsConfig:
        if self.heartbeat_seconds >= self.lease_seconds:
            msg = "jobs.heartbeat_seconds must be lower than jobs.lease_seconds"
            raise ValueError(msg)
        return self


class WebhooksConfig(BaseModel):
    """Bounded outbound webhook dispatcher and signing configuration."""

    enabled: bool = True
    dispatcher_enabled: bool = True
    signing_key: str = "development-only-webhook-signing-key"
    dispatcher_poll_seconds: float = Field(default=1.0, ge=0.1, le=60.0)
    dispatcher_batch_size: int = Field(default=50, ge=1, le=500)
    delivery_timeout_seconds: float = Field(default=10.0, ge=0.5, le=120.0)
    delivery_lease_seconds: int = Field(default=60, ge=5, le=600)
    max_attempts: int = Field(default=6, ge=1, le=20)
    retry_base_seconds: float = Field(default=5.0, ge=0.1, le=3600.0)
    retry_max_seconds: float = Field(default=3600.0, ge=1.0, le=86_400.0)
    response_excerpt_chars: int = Field(default=1000, ge=0, le=10_000)


class KnowledgeConfig(BaseModel):
    """Knowledge ingestion limits."""

    max_upload_bytes: int = Field(default=50 * 1024 * 1024, ge=1)


class MalwareScannerBackend(StrEnum):
    DISABLED = "disabled"
    CLAMAV = "clamav"


class MalwareScanConfig(BaseModel):
    """Upload malware scanning; disabled is explicit for development/testing only."""

    backend: MalwareScannerBackend = MalwareScannerBackend.DISABLED
    host: str = "localhost"
    port: int = Field(default=3310, ge=1, le=65535)
    timeout_seconds: float = Field(default=15.0, ge=0.1, le=120.0)


class ParsingConfig(BaseModel):
    """PDF text extraction and parse-quality configuration."""

    min_page_quality_score: float = Field(default=0.55, ge=0.0, le=1.0)
    min_document_success_ratio: float = Field(default=0.2, ge=0.0, le=1.0)
    min_text_chars: int = Field(default=20, ge=1, le=10_000)
    pdf_text_parsers: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["pymupdf", "pdfium"]
    )

    @field_validator("pdf_text_parsers", mode="before")
    @classmethod
    def _split_pdf_text_parsers(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped or stripped.startswith("["):
                return value
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return value


class ChunkingStrategy(StrEnum):
    """Supported text chunking strategies."""

    AUTO = "auto"
    MARKDOWN = "markdown"
    HEADING = "heading"
    STRUCTURE = "structure"
    SEMANTIC = "semantic"
    RECURSIVE_FALLBACK = "recursive_fallback"
    RECURSIVE_CHARACTER = "recursive_character"


class ChunkingConfig(BaseModel):
    """Approximate token-based chunking defaults for the knowledge ingestion pipeline.

    Token counts are approximate because embedding models tokenize differently.
    """

    strategy: ChunkingStrategy = ChunkingStrategy.AUTO
    target_tokens: int = Field(default=250, ge=50, le=4096)
    max_tokens: int = Field(default=400, ge=100, le=8192)
    min_tokens: int = Field(default=50, ge=1, le=2048)
    overlap_tokens: int = Field(default=50, ge=0, le=1024)
    structure_score_threshold: float = Field(default=0.55, ge=0.0, le=1.0)
    long_block_token_threshold: int = Field(default=600, ge=100, le=8192)
    similarity_drop_threshold: float = Field(default=0.35, ge=0.0, le=1.0)
    semantic_batch_size: int = Field(default=32, ge=1, le=256)
    chunker_version: str = "3.0.0"
    token_count_method: str = "unicode_property_v1"
    ocr_confidence_threshold: float = Field(default=0.5, ge=0.0, le=1.0)


class EmbeddingBackend(StrEnum):
    """Supported embedding provider backends."""

    HASH = "hash"
    OLLAMA = "ollama"
    OPENAI = "openai"
    GEMINI = "gemini"
    COHERE = "cohere"


class EmbeddingConfig(BaseModel):
    """Dense-vector provider used for indexing and query search.

    Changing backend or model requires a new ``embedding_set_version`` and a
    full rebuild; never mix providers in one active set. Hosted_managed uses
    Cohere ``embed-v4.0`` at 1024 dimensions. Local tests keep hash embeddings.
    """

    backend: EmbeddingBackend = EmbeddingBackend.HASH
    model: str = "text-embedding-3-large"
    dimensions: int = Field(default=1024, ge=1, le=2000)
    batch_size: int = Field(default=32, ge=1, le=256)
    ollama_base_url: str = "http://localhost:11434"
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com"
    gemini_api_key: str | None = None
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    provider_version: str = "1"


class OcrBackend(StrEnum):
    """Supported OCR provider backends."""

    NOOP = "noop"
    PADDLE = "paddle"
    GOOGLE_VISION = "google_vision"


class OcrConfig(BaseModel):
    """OCR provider configuration — disabled by default.

    Enable in production when scanned or Bangla/custom-font PDFs need Google
    Vision. Native parse still runs first except for Bangla OCR-first pages.
    """

    enabled: bool = False
    backend: OcrBackend = OcrBackend.NOOP
    bangla_backend: OcrBackend = OcrBackend.NOOP
    lang: str = "en"
    use_gpu: bool = False
    google_api_key: str | None = None
    google_endpoint: str = "https://vision.googleapis.com/v1/images:annotate"
    google_timeout_seconds: float = Field(default=30.0, gt=0.0, le=300.0)
    google_max_attempts: int = Field(default=3, ge=1, le=10)
    bangla_min_ratio: float = Field(default=0.10, ge=0.0, le=1.0)
    max_ocr_pages_per_document: int = Field(default=100, ge=1, le=10_000)
    min_text_chars: int = Field(default=20, ge=1, le=10_000)
    min_image_area_ratio: float = Field(
        default=0.08,
        ge=0.01,
        le=1.0,
        description="Minimum image area (fraction of page) for per-image OCR",
    )
    dpi: int = Field(default=200, ge=72, le=600)
    min_page_confidence: float = Field(default=0.3, ge=0.0, le=1.0)


class RetrievalStrategy(StrEnum):
    """Active retrieval pipeline strategy."""

    SEMANTIC = "semantic"
    HYBRID = "hybrid"


class RerankerBackend(StrEnum):
    """Supported reranker provider backends."""

    NOOP = "noop"
    LEXICAL = "lexical"
    EMBEDDING = "embedding"
    EMBEDDING_MAX = "embedding_max"
    COHERE = "cohere"


class RerankMode(StrEnum):
    """When the multilingual reranker may run after RRF.

    Platform default is ALWAYS. Projects may inherit or opt down to
    cross-language-only or off. None in a sparse Project payload means inherit.
    """

    ALWAYS = "always"
    CROSS_LANGUAGE = "cross_language"
    OFF = "off"


class EvidenceScoreMode(StrEnum):
    """Calibrated semantic score used by the pre-generation evidence gate."""

    WHOLE_CHUNK = "whole_chunk"
    PASSAGE_MAX = "passage_max"


class EvidenceGateMode(StrEnum):
    """Whether a failed pre-generation evidence score may block the LLM."""

    ENFORCE = "enforce"
    OBSERVE = "observe"


class RetrievalConfig(BaseModel):
    """Retrieval pipeline and search defaults.

    Hybrid dense + BM25 + RRF is the production path. ``rerank_mode`` is the
    platform default (Always); Projects override with Inherit / Always /
    Cross-language / Off. ``embedding_set_version`` identifies the active
    vector representation — bump it when changing embedding provider or model.
    """

    auto_embed: bool = True
    auto_index: bool = True
    default_top_k: int = Field(default=10, ge=1, le=100)
    score_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    # Deployment-level representation id. Hosted_managed Cohere embed-v4 uses 3.
    embedding_set_version: int = Field(default=2, ge=1)
    filterable_metadata_keys: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["source", "tags", "ocr_confidence"]
    )
    strategy: RetrievalStrategy = RetrievalStrategy.HYBRID
    semantic_candidate_top_k: int = Field(default=50, ge=1, le=200)
    hnsw_ef_search: int = Field(default=100, ge=1, le=1000)
    keyword_candidate_top_k: int = Field(default=50, ge=1, le=200)
    rrf_k: int = Field(default=60, ge=1, le=500)
    semantic_weight: float = Field(default=1.0, ge=0.0, le=10.0)
    keyword_weight: float = Field(default=1.0, ge=0.0, le=10.0)
    rerank_enabled: bool = True
    # Always rerank after RRF unless a Project opts down. Cross-language skips
    # the paid call when inventory says query and corpus share a language.
    rerank_mode: RerankMode = RerankMode.ALWAYS
    rerank_top_n: int = Field(default=20, ge=1, le=100)
    rerank_candidate_window: int = Field(default=25, ge=1, le=100)
    rerank_return_n: int = Field(default=8, ge=1, le=100)
    rerank_score_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    reranker_backend: RerankerBackend = RerankerBackend.NOOP
    language_metadata_schema_version: str = "2026-08-18.v1"
    fts_regconfig: str = "simple"
    min_ocr_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    max_chunks_per_document: int = Field(default=4, ge=1, le=100)
    max_chunks_per_section: int = Field(default=2, ge=1, le=100)
    deduplicate_by_content_hash: bool = True
    passage_scoring_enabled: bool = False
    passage_window_tokens: int = Field(default=96, ge=16, le=512)
    passage_overlap_tokens: int = Field(default=24, ge=0, le=256)
    passage_min_tokens: int = Field(default=32, ge=8, le=256)

    @field_validator("filterable_metadata_keys", mode="before")
    @classmethod
    def _split_filterable_keys(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped or stripped.startswith("["):
                return value
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def validate_passage_windows(self) -> RetrievalConfig:
        if self.passage_overlap_tokens >= self.passage_window_tokens:
            raise ValueError("passage_overlap_tokens must be smaller than passage_window_tokens")
        if self.passage_min_tokens > self.passage_window_tokens:
            raise ValueError("passage_min_tokens must not exceed passage_window_tokens")
        return self


class LLMBackend(StrEnum):
    """Supported LLM provider backends."""

    ECHO = "echo"
    OPENAI = "openai"
    OPENAI_COMPATIBLE = "openai_compatible"
    OLLAMA = "ollama"
    GEMINI = "gemini"


class LLMConfig(BaseModel):
    """Chat/generation provider. Hosted profiles require OpenAI; local tests use echo.

    ``hosted_managed`` generation is ``gpt-5.6-luna``. Query translation uses a
    separate nano model and does not inherit this field.
    """

    backend: LLMBackend = LLMBackend.ECHO
    model: str = "gpt-4o-mini"
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4096, ge=1, le=128_000)
    request_timeout_seconds: float = Field(default=120.0, ge=1.0, le=600.0)
    ollama_base_url: str = "http://localhost:11434"
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com"
    gemini_api_key: str | None = None
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    provider_version: str = "1"


class QueryTranslationConfig(BaseModel):
    """Optional one-shot query rewrite for hybrid retrieval.

    Runs only when the active build language inventory contains a supported
    language different from the query. Original dense + BM25 always remain.
    Projects override with Inherit / On / Off. Disabled by default so pytest
    and local hash/echo stacks do not require an LLM key.
    """

    enabled: bool = False
    backend: LLMBackend = LLMBackend.OPENAI
    model: str = "gpt-5-nano"
    prompt_version: str = "retrieval-translation-v2"
    max_output_tokens: int = Field(default=4096, ge=16, le=8192)
    request_timeout_seconds: float = Field(default=45.0, ge=1.0, le=180.0)
    retry_max_attempts: int = Field(default=1, ge=0, le=3)
    target_languages: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["bn", "en"])

    @field_validator("target_languages", mode="before")
    @classmethod
    def _split_target_languages(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped or stripped.startswith("["):
                return value
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return value


class CohereConfig(BaseModel):
    """Shared Cohere credential and base connection for embeddings and reranking.

    Canonical secret is ``APE_COHERE__API_KEY``. Feature-specific models stay on
    ``APE_EMBEDDING__MODEL`` and ``APE_RERANKER__COHERE_MODEL``.
    ``APE_RERANKER__COHERE_API_KEY`` / ``APE_RERANKER__COHERE_BASE_URL`` remain
    temporary fallbacks for older rerank-only deploys.
    """

    api_key: str | None = None
    base_url: str = "https://api.cohere.com"


class RerankerProviderConfig(BaseModel):
    """Managed reranker model and timeout (not the canonical key or base URL).

    Hosted_managed uses ``rerank-v4.0-pro``. Missing key or provider failure
    degrades to RRF + cosine evidence; it must not block API startup.
    """

    # Legacy fallback only. Prefer APE_COHERE__API_KEY and APE_COHERE__BASE_URL.
    cohere_api_key: str | None = None
    cohere_base_url: str = "https://api.cohere.com"
    cohere_model: str = "rerank-v4.0-pro"
    request_timeout_seconds: float = Field(default=30.0, ge=1.0, le=120.0)
    provider_version: str = "1"


class GenerationRetentionMode(StrEnum):
    """Caller payload retention modes for contextual generation traces."""

    NONE = "none"
    METADATA_ONLY = "metadata_only"
    FULL = "full"


class GenerationConfig(BaseModel):
    """Synchronous contextual generation limits and retention policy."""

    max_request_bytes: int = Field(default=256 * 1024, ge=1024, le=10 * 1024 * 1024)
    max_context_bytes: int = Field(default=200 * 1024, ge=1, le=10 * 1024 * 1024)
    max_schema_bytes: int = Field(default=32 * 1024, ge=128, le=1024 * 1024)
    max_context_depth: int = Field(default=12, ge=1, le=64)
    max_context_nodes: int = Field(default=5000, ge=1, le=100_000)
    default_retention: GenerationRetentionMode = GenerationRetentionMode.NONE
    allow_full_retention: bool = True


class VerifyCacheBackend(StrEnum):
    """Verified-key cache storage backend."""

    REDIS = "redis"
    MEMORY = "memory"


class AuthConfig(BaseModel):
    """Machine API-key and browser Super Admin authentication settings."""

    enabled: bool = False
    key_pepper: str | None = None
    verify_cache_enabled: bool = True
    verify_cache_ttl_seconds: int = Field(default=60, ge=1, le=3600)
    verify_cache_backend: VerifyCacheBackend = VerifyCacheBackend.REDIS
    rate_limit_enabled: bool = True
    rate_limit_requests: int = Field(default=1000, ge=1)
    rate_limit_window_seconds: int = Field(default=60, ge=1)
    rate_limit_fail_open: bool = False
    admin_login_rate_limit_requests: int = Field(default=5, ge=1, le=100)
    admin_login_rate_limit_window_seconds: int = Field(default=60, ge=1, le=3600)
    admin_jwt_secret: str | None = None
    # Keep access tokens bounded while allowing a workday session. Refresh
    # tokens remain the preferred way to keep a browser session alive.
    admin_access_token_expire_minutes: int = Field(default=15, ge=1, le=8 * 60)
    admin_refresh_token_expire_days: int = Field(default=14, ge=1, le=90)
    admin_cookie_secure: bool = False
    admin_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    admin_cookie_domain: str | None = None


class ChatConfig(BaseModel):
    """Chat / RAG orchestration defaults.

    Evidence gate: when a true reranker applied, its relevance is the only
    learned admit signal; cosine + lexical rescue are fallbacks. Thresholds are
    provider-specific calibration, not universal constants. Hosted_managed
    examples ``enforce`` with the current defaults; use ``observe`` only while
    measuring a corpus in Test Lab.
    """

    retrieval_top_k: int = Field(default=10, ge=1, le=100)
    max_context_chunks: int = Field(default=8, ge=1, le=50)
    context_char_budget: int = Field(default=12_000, ge=500, le=200_000)
    max_history_messages: int = Field(default=20, ge=0, le=200)
    system_prompt_version: str = "v4"
    include_citations: bool = True
    citation_excerpt_max_chars: int = Field(default=200, ge=0, le=2000)
    evidence_score_mode: EvidenceScoreMode = EvidenceScoreMode.WHOLE_CHUNK
    evidence_gate_mode: EvidenceGateMode = EvidenceGateMode.ENFORCE
    minimum_semantic_evidence_score: float = Field(default=0.35, ge=0.0, le=1.0)
    # Below the primary bar so same-language OCR/table hits that land just under
    # 0.35 can still pass when query tokens strongly overlap the evidence.
    # Cross-language near-misses typically have ~0 coverage, so they still refuse.
    lexical_corroboration_floor_score: float = Field(default=0.30, ge=0.0, le=1.0)
    lexical_corroboration_coverage: float = Field(default=0.50, ge=0.0, le=1.0)
    # Provider-specific calibration. Keep observe until this pair is measured.
    minimum_reranker_evidence_score: float = Field(default=0.40, ge=0.0, le=1.0)
    minimum_evidence_score: float | None = Field(default=None, deprecated=True)
    minimum_query_token_coverage: float | None = Field(default=None, deprecated=True)
    minimum_claim_token_coverage: float = Field(default=0.35, ge=0.0, le=1.0)
    minimum_claim_semantic_score: float = Field(default=0.25, ge=0.0, le=1.0)
    claim_semantic_reject_floor: float = Field(default=0.15, ge=0.0, le=1.0)
    insufficient_evidence_message: str = (
        "I don't have enough evidence in the indexed sources to answer that question."
    )
    auto_title_max_chars: int = Field(default=80, ge=10, le=255)

    @model_validator(mode="after")
    def reject_deprecated_evidence_thresholds(self) -> ChatConfig:
        deprecated = []
        if self.__dict__.get("minimum_evidence_score") is not None:
            deprecated.append("APE_CHAT__MINIMUM_EVIDENCE_SCORE")
        if self.__dict__.get("minimum_query_token_coverage") is not None:
            deprecated.append("APE_CHAT__MINIMUM_QUERY_TOKEN_COVERAGE")
        if deprecated:
            msg = (
                f"{', '.join(deprecated)} is deprecated; configure "
                "APE_CHAT__MINIMUM_SEMANTIC_EVIDENCE_SCORE and the "
                "APE_CHAT__LEXICAL_CORROBORATION_* settings instead"
            )
            raise ValueError(msg)
        if self.lexical_corroboration_floor_score > self.minimum_semantic_evidence_score:
            msg = (
                "lexical_corroboration_floor_score must not exceed minimum_semantic_evidence_score"
            )
            raise ValueError(msg)
        if self.claim_semantic_reject_floor > self.minimum_claim_semantic_score:
            msg = "claim_semantic_reject_floor must not exceed minimum_claim_semantic_score"
            raise ValueError(msg)
        return self


class EvaluationConfig(BaseModel):
    """Reproducible quality-run defaults and acceptance thresholds."""

    evaluator_version: str = "quality-v3"
    default_top_k: int = Field(default=5, ge=1, le=100)
    max_cases_per_dataset: int = Field(default=500, ge=1, le=10_000)
    minimum_recall_at_k: float = Field(default=0.80, ge=0.0, le=1.0)
    minimum_rank_1_accuracy: float = Field(default=0.80, ge=0.0, le=1.0)
    minimum_cross_lingual_recall_at_k: float = Field(default=0.75, ge=0.0, le=1.0)
    minimum_filtered_correctness: float = Field(default=0.95, ge=0.0, le=1.0)
    maximum_false_refusal_rate: float = Field(default=0.10, ge=0.0, le=1.0)
    maximum_false_accept_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    maximum_accepted_without_relevant_evidence_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    minimum_groundedness: float = Field(default=0.80, ge=0.0, le=1.0)
    minimum_citation_coverage: float = Field(default=0.80, ge=0.0, le=1.0)
    maximum_p95_latency_ms: float = Field(default=750.0, ge=1.0)
    maximum_metric_regression: float = Field(default=0.02, ge=0.0, le=1.0)
    minimum_reranker_ndcg_gain: float = Field(default=0.02, ge=0.0, le=1.0)
    maximum_reranker_latency_penalty_ms: float = Field(default=150.0, ge=0.0)
    reranker_candidates: Annotated[list[RerankerBackend], NoDecode] = Field(
        default_factory=lambda: [
            RerankerBackend.LEXICAL,
            RerankerBackend.EMBEDDING,
            RerankerBackend.EMBEDDING_MAX,
        ]
    )

    @field_validator("reranker_candidates", mode="before")
    @classmethod
    def _split_reranker_candidates(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped or stripped.startswith("["):
                return value
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return value


class RequestOverrideMode(StrEnum):
    """Migration policy for deprecated external AI-policy overrides."""

    COMPATIBILITY = "compatibility"
    STRICT = "strict"


class SourcePolicyDeploymentCap(StrEnum):
    """Deployment-wide maximum source-policy enforcement level."""

    OFF = "off"
    OBSERVE = "observe"
    ENFORCE = "enforce"


class AIConfigPolicy(BaseModel):
    """Deployment safety bounds around Project AI configuration."""

    request_override_mode: RequestOverrideMode = RequestOverrideMode.COMPATIBILITY
    max_request_top_k: int = Field(default=100, ge=1, le=100)
    source_policy_deployment_cap: SourcePolicyDeploymentCap = SourcePolicyDeploymentCap.ENFORCE
    enabled_retrieval_strategies: Annotated[list[RetrievalStrategy], NoDecode] = Field(
        default_factory=lambda: [RetrievalStrategy.SEMANTIC, RetrievalStrategy.HYBRID]
    )

    @field_validator("enabled_retrieval_strategies", mode="before")
    @classmethod
    def _split_enabled_strategies(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped or stripped.startswith("["):
                return value
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return value


class Settings(BaseSettings):
    """Root settings object aggregating all configuration sections."""

    model_config = SettingsConfigDict(
        env_prefix="APE_",
        env_nested_delimiter="__",
        env_file=_settings_env_files(),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app: AppConfig = Field(default_factory=AppConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    cors: CORSConfig = Field(default_factory=CORSConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    test_database: DisposableDatabaseConfig = Field(default_factory=DisposableDatabaseConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    minio: MinioConfig = Field(default_factory=MinioConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    jobs: JobsConfig = Field(default_factory=JobsConfig)
    webhooks: WebhooksConfig = Field(default_factory=WebhooksConfig)
    knowledge: KnowledgeConfig = Field(default_factory=KnowledgeConfig)
    malware_scan: MalwareScanConfig = Field(default_factory=MalwareScanConfig)
    parsing: ParsingConfig = Field(default_factory=ParsingConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    ocr: OcrConfig = Field(default_factory=OcrConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    query_translation: QueryTranslationConfig = Field(default_factory=QueryTranslationConfig)
    cohere: CohereConfig = Field(default_factory=CohereConfig)
    reranker: RerankerProviderConfig = Field(default_factory=RerankerProviderConfig)
    generation: GenerationConfig = Field(default_factory=GenerationConfig)
    chat: ChatConfig = Field(default_factory=ChatConfig)
    evaluation: EvaluationConfig = Field(default_factory=EvaluationConfig)
    ai_policy: AIConfigPolicy = Field(default_factory=AIConfigPolicy)
    auth: AuthConfig = Field(default_factory=AuthConfig)

    def resolved_cohere_api_key(self) -> str:
        """Canonical shared Cohere key, then legacy reranker-only key."""
        canonical = (self.cohere.api_key or "").strip()
        if canonical:
            return canonical
        return (self.reranker.cohere_api_key or "").strip()

    def resolved_cohere_base_url(self) -> str:
        """Canonical shared Cohere base URL, then legacy reranker-only base URL."""
        shared = self.cohere.base_url.rstrip("/")
        if shared != "https://api.cohere.com":
            return self.cohere.base_url
        return self.reranker.cohere_base_url


@lru_cache
def get_settings() -> Settings:
    """Return a cached ``Settings`` instance for the process lifetime.

    Cached so configuration is parsed once and shared everywhere via
    dependency injection. Tests can clear the cache with
    ``get_settings.cache_clear()``.
    """
    return Settings()
