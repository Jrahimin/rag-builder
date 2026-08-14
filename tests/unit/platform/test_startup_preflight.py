"""Bounded capability-preflight success and dimension-failure tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from app.core.config import OcrBackend, OcrConfig, Settings, StorageConfig
from app.platform.providers.contracts.embedding import EmbeddingBatchResult
from app.platform.system.preflight_service import StartupPreflightService
from app.platform.system.schemas import DependencyState


async def test_preflight_reports_provider_dimension_mismatch(monkeypatch) -> None:
    settings = Settings(storage=StorageConfig(local_root="./storage"))
    database = MagicMock(check=AsyncMock())
    redis = MagicMock(check=AsyncMock())
    storage = MagicMock(check=AsyncMock())

    embedder = MagicMock()
    embedder.embed_texts = AsyncMock(
        return_value=EmbeddingBatchResult(
            vectors=[[0.0, 1.0]],
            provider="fake",
            model="fake",
            dimensions=2,
            provider_version="1",
        )
    )
    llm = MagicMock(generate=AsyncMock())
    reranker = MagicMock(rerank=AsyncMock())
    monkeypatch.setattr(
        "app.platform.system.preflight_service.create_embedding_provider",
        lambda _settings: embedder,
    )
    monkeypatch.setattr(
        "app.platform.system.preflight_service.create_llm_provider",
        lambda _settings: llm,
    )
    monkeypatch.setattr(
        "app.platform.system.preflight_service.create_reranker_provider",
        lambda _settings: reranker,
    )

    result = await StartupPreflightService(
        settings=settings,
        database=database,
        redis=redis,
        storage=storage,
    ).run()

    embedding = next(check for check in result.checks if check.name == "embedding_provider")
    assert result.status == "degraded"
    assert embedding.state is DependencyState.DEGRADED
    assert "RuntimeError" in (embedding.detail or "")
    assert llm.generate.await_count == 1


async def test_core_preflight_does_not_wait_for_external_ai_providers(monkeypatch) -> None:
    settings = Settings(storage=StorageConfig(local_root="./storage"))
    database = MagicMock(check=AsyncMock())
    redis = MagicMock(check=AsyncMock())
    storage = MagicMock(check=AsyncMock())
    embedding_factory = MagicMock()
    monkeypatch.setattr(
        "app.platform.system.preflight_service.create_embedding_provider",
        embedding_factory,
    )

    result = await StartupPreflightService(
        settings=settings,
        database=database,
        redis=redis,
        storage=storage,
    ).run_core()

    assert result.status == "ready"
    assert embedding_factory.call_count == 0
    assert next(
        check for check in result.checks if check.name == "embedding_provider"
    ).state is DependencyState.DEGRADED


async def test_preflight_marks_disabled_ocr_as_skipped(monkeypatch) -> None:
    settings = Settings()
    database = MagicMock(check=AsyncMock())
    redis = MagicMock(check=AsyncMock())
    storage = MagicMock(check=AsyncMock())
    monkeypatch.setattr(
        "app.platform.system.preflight_service.create_embedding_provider",
        lambda _settings: MagicMock(
            embed_texts=AsyncMock(
                return_value=EmbeddingBatchResult(
                    vectors=[[0.0] * settings.embedding.dimensions],
                    provider="hash",
                    model="hash",
                    dimensions=settings.embedding.dimensions,
                    provider_version="1",
                )
            )
        ),
    )
    monkeypatch.setattr(
        "app.platform.system.preflight_service.create_llm_provider",
        lambda _settings: MagicMock(generate=AsyncMock()),
    )
    monkeypatch.setattr(
        "app.platform.system.preflight_service.create_reranker_provider",
        lambda _settings: MagicMock(rerank=AsyncMock()),
    )

    result = await StartupPreflightService(
        settings=settings,
        database=database,
        redis=redis,
        storage=storage,
    ).run()
    ocr = next(check for check in result.checks if check.name == "ocr_provider")
    assert ocr.state is DependencyState.SKIPPED


async def test_preflight_warms_cached_ocr_provider(monkeypatch) -> None:
    settings = Settings(ocr=OcrConfig(enabled=True, backend=OcrBackend.PADDLE))
    service = StartupPreflightService(
        settings=settings,
        database=MagicMock(),
        redis=MagicMock(),
        storage=MagicMock(),
    )
    provider = MagicMock()
    get_provider = MagicMock(return_value=provider)
    monkeypatch.setattr(
        "app.platform.system.preflight_service.get_ocr_provider",
        get_provider,
    )

    result = await service._check_ocr()

    assert result.state is DependencyState.OK
    get_provider.assert_called_once_with(lang="en", settings=settings)


async def test_preflight_warms_bangla_only_backend_without_network_probe(monkeypatch) -> None:
    settings = Settings(
        ocr=OcrConfig(
            enabled=True,
            backend=OcrBackend.NOOP,
            bangla_backend=OcrBackend.GOOGLE_VISION,
            google_api_key="test-key",
        )
    )
    service = StartupPreflightService(
        settings=settings,
        database=MagicMock(),
        redis=MagicMock(),
        storage=MagicMock(),
    )
    get_provider = MagicMock(return_value=MagicMock())
    monkeypatch.setattr(
        "app.platform.system.preflight_service.get_ocr_provider",
        get_provider,
    )

    result = await service._check_ocr()

    assert result.state is DependencyState.OK
    get_provider.assert_called_once_with(lang="bn", settings=settings)
