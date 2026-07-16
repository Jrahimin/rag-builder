"""Unit tests for the configuration layer."""

from __future__ import annotations

import pytest

from app.core.config import (
    AppConfig,
    CORSConfig,
    DatabaseConfig,
    DisposableDatabaseConfig,
    EmbeddingConfig,
    Environment,
    MinioConfig,
    RedisConfig,
    get_settings,
)

pytestmark = pytest.mark.unit


def test_database_async_and_sync_dsn() -> None:
    db = DatabaseConfig(host="db", port=5432, user="u", password="p", name="ape")
    assert db.async_dsn == "postgresql+asyncpg://u:p@db:5432/ape"
    assert db.sync_dsn == "postgresql+psycopg://u:p@db:5432/ape"


def test_database_dsn_escapes_special_characters() -> None:
    db = DatabaseConfig(user="u", password="p@ss:word/!", host="db", name="ape")
    # The raw special characters must be percent-encoded in the URL.
    assert "p@ss:word/!" not in db.async_dsn
    assert "@db:5432/ape" in db.async_dsn


def test_redis_dsn_with_and_without_password() -> None:
    assert RedisConfig(host="r", port=6379, db=1).dsn == "redis://r:6379/1"
    assert (
        RedisConfig(host="r", port=6379, db=0, password="secret").dsn == "redis://:secret@r:6379/0"
    )


def test_minio_urls() -> None:
    assert MinioConfig(endpoint="m:9000").url == "http://m:9000"
    assert MinioConfig(endpoint="m:9000", secure=True).url == "https://m:9000"


def test_cors_accepts_comma_separated_string() -> None:
    cfg = CORSConfig(allow_origins="http://a.com, http://b.com")
    assert cfg.allow_origins == ["http://a.com", "http://b.com"]


def test_cors_accepts_list() -> None:
    cfg = CORSConfig(allow_origins=["http://a.com"])
    assert cfg.allow_origins == ["http://a.com"]


def test_cors_accepts_wildcard_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APE_CORS__ALLOW_ORIGINS", "*")
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.cors.allow_origins == ["*"]
    get_settings.cache_clear()


def test_app_environment_flags() -> None:
    assert AppConfig(env=Environment.PRODUCTION).is_production is True
    assert AppConfig(env=Environment.TESTING).is_testing is True
    assert AppConfig(env=Environment.DEVELOPMENT).is_development is True


def test_disposable_database_defaults() -> None:
    cfg = DisposableDatabaseConfig()
    assert cfg.name == "ape_test"
    assert cfg.allow_migrations is False


def test_embedding_dimension_policy_caps_pgvector_hnsw_at_2000() -> None:
    assert EmbeddingConfig(dimensions=2000).dimensions == 2000
    with pytest.raises(ValueError):
        EmbeddingConfig(dimensions=2001)


def test_integration_db_guard_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APE_APP__ENV", "testing")
    monkeypatch.setenv("APE_DATABASE__NAME", "ape")
    monkeypatch.setenv("APE_TEST_DATABASE__NAME", "ape_test")
    monkeypatch.delenv("APE_TEST_DATABASE__ALLOW_MIGRATIONS", raising=False)
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.database.name != settings.test_database.name
    get_settings.cache_clear()
