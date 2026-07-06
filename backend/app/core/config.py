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
from typing import Annotated

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy import URL


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
    version: str = "0.1.0"
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


class QdrantConfig(BaseModel):
    """Qdrant vector database connection configuration.

    NOTE: This holds *connectivity* settings only. Actual vector operations
    must go through a ``BaseVectorStoreProvider`` abstraction (added later),
    keeping the application core vector-DB agnostic.
    """

    host: str = "localhost"
    port: int = 6333
    grpc_port: int = 6334
    prefer_grpc: bool = False
    https: bool = False
    api_key: str | None = None

    @property
    def url(self) -> str:
        scheme = "https" if self.https else "http"
        return f"{scheme}://{self.host}:{self.port}"


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


class KnowledgeConfig(BaseModel):
    """Knowledge ingestion limits."""

    max_upload_bytes: int = Field(default=50 * 1024 * 1024, ge=1)


class ChunkingStrategy(StrEnum):
    """Supported text chunking strategies."""

    RECURSIVE_CHARACTER = "recursive_character"


class ChunkingConfig(BaseModel):
    """Text chunking defaults for the knowledge ingestion pipeline."""

    strategy: ChunkingStrategy = ChunkingStrategy.RECURSIVE_CHARACTER
    chunk_size: int = Field(default=1000, ge=100, le=16_000)
    chunk_overlap: int = Field(default=200, ge=0, le=4000)


class EmbeddingBackend(StrEnum):
    """Supported embedding provider backends."""

    HASH = "hash"
    OLLAMA = "ollama"
    OPENAI = "openai"
    GEMINI = "gemini"


class EmbeddingConfig(BaseModel):
    """Embedding provider configuration."""

    backend: EmbeddingBackend = EmbeddingBackend.HASH
    model: str = "nomic-embed-text"
    dimensions: int = Field(default=384, ge=1, le=4096)
    batch_size: int = Field(default=32, ge=1, le=256)
    ollama_base_url: str = "http://localhost:11434"
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com"
    gemini_api_key: str | None = None
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    provider_version: str = "1"


class VectorStoreBackend(StrEnum):
    """Supported vector store backends."""

    QDRANT = "qdrant"
    MEMORY = "memory"


class VectorStoreConfig(BaseModel):
    """Vector store collection configuration (connectivity via QdrantConfig)."""

    backend: VectorStoreBackend = VectorStoreBackend.QDRANT
    collection_name: str = "ape_chunks"


class RetrievalConfig(BaseModel):
    """Retrieval pipeline and search defaults."""

    auto_embed: bool = True
    auto_index: bool = True
    default_top_k: int = Field(default=10, ge=1, le=100)
    score_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    embedding_set_version: int = Field(default=1, ge=1)
    filterable_metadata_keys: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["source", "tags"]
    )

    @field_validator("filterable_metadata_keys", mode="before")
    @classmethod
    def _split_filterable_keys(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped or stripped.startswith("["):
                return value
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return value


class LLMBackend(StrEnum):
    """Supported LLM provider backends."""

    ECHO = "echo"
    OPENAI = "openai"
    OPENAI_COMPATIBLE = "openai_compatible"
    OLLAMA = "ollama"
    GEMINI = "gemini"


class LLMConfig(BaseModel):
    """LLM provider configuration."""

    backend: LLMBackend = LLMBackend.ECHO
    model: str = "gpt-4o-mini"
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, ge=1, le=128_000)
    request_timeout_seconds: float = Field(default=120.0, ge=1.0, le=600.0)
    ollama_base_url: str = "http://localhost:11434"
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com"
    gemini_api_key: str | None = None
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    provider_version: str = "1"


class ChatConfig(BaseModel):
    """Chat / RAG orchestration defaults."""

    retrieval_top_k: int = Field(default=10, ge=1, le=100)
    max_context_chunks: int = Field(default=8, ge=1, le=50)
    context_char_budget: int = Field(default=12_000, ge=500, le=200_000)
    max_history_messages: int = Field(default=20, ge=0, le=200)
    system_prompt_version: str = "v1"
    include_citations: bool = True
    citation_excerpt_max_chars: int = Field(default=200, ge=0, le=2000)
    auto_title_max_chars: int = Field(default=80, ge=10, le=255)


class Settings(BaseSettings):
    """Root settings object aggregating all configuration sections."""

    model_config = SettingsConfigDict(
        env_prefix="APE_",
        env_nested_delimiter="__",
        env_file=(".env",) if os.environ.get("APE_APP__ENV") != "testing" else None,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app: AppConfig = Field(default_factory=AppConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    cors: CORSConfig = Field(default_factory=CORSConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    test_database: DisposableDatabaseConfig = Field(default_factory=DisposableDatabaseConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    qdrant: QdrantConfig = Field(default_factory=QdrantConfig)
    minio: MinioConfig = Field(default_factory=MinioConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    jobs: JobsConfig = Field(default_factory=JobsConfig)
    knowledge: KnowledgeConfig = Field(default_factory=KnowledgeConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    vector_store: VectorStoreConfig = Field(default_factory=VectorStoreConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    chat: ChatConfig = Field(default_factory=ChatConfig)


@lru_cache
def get_settings() -> Settings:
    """Return a cached ``Settings`` instance for the process lifetime.

    Cached so configuration is parsed once and shared everywhere via
    dependency injection. Tests can clear the cache with
    ``get_settings.cache_clear()``.
    """
    return Settings()
