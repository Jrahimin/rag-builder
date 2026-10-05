"""Expense controls preserve exact embeddings and fail before paid dispatch."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from dataclasses import replace
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import ProviderCostsConfig
from app.platform.providers.contracts.embedding import (
    BaseEmbeddingProvider,
    EmbeddingBatchResult,
    EmbeddingPurpose,
)
from app.platform.providers.errors import ProviderError
from app.platform.providers.provider_work import (
    CachedEmbeddingProvider,
    ProviderBudgetError,
    ProviderWorkScope,
    attached_provider_scope,
    billed_usage,
    current_provider_scope,
    metered_cohere_post,
)

pytestmark = pytest.mark.unit


class MemoryStore:
    def __init__(self):
        self.data = {}
        self.lock = asyncio.Lock()

    @asynccontextmanager
    async def cache_session(self, project):
        async with self.lock:
            transaction = dict(self.data)
            yield transaction
            self.data = transaction

    async def vectors(self, session, project, keys):
        return {key: session[project, key] for key in keys if (project, key) in session}

    async def seed_vectors(self, *args):
        return {}

    async def save_vectors(self, session, project, rows, ttl):
        session.update({(project, key): vector for key, (_, vector) in rows.items()})


class Embeddings(BaseEmbeddingProvider):
    provider_name = "cohere"
    model_name = "embed-v4.0"
    dimensions = 2
    provider_version = "v1"
    cache_namespace = "https://api.cohere.com"

    def __init__(self):
        self.calls = []
        self.fail_call = None

    async def embed_texts(self, texts, *, purpose=EmbeddingPurpose.DOCUMENT):
        self.calls.append((texts, purpose))
        await asyncio.sleep(0)
        if len(self.calls) == self.fail_call:
            raise ProviderError("timeout", provider_name="cohere")
        return EmbeddingBatchResult(
            [[float(len(t)), 1.0] for t in texts],
            self.provider_name,
            self.model_name,
            self.dimensions,
            self.provider_version,
            len(texts) * 10,
        )


def work(store=None, **config):
    return ProviderWorkScope(
        uuid.uuid4(),
        str(uuid.uuid4()),
        "chat",
        "testing",
        3,
        ProviderCostsConfig(enabled=True, cache_enabled=True, **config),
        store or MemoryStore(),
    )


async def test_repeated_build_and_new_document_pay_only_for_unique_new_inputs():
    raw = Embeddings()
    provider = CachedEmbeddingProvider(raw)
    scope = work()
    with attached_provider_scope(scope):
        first = await provider.embed_texts(["a", "b", "a"])
        second = await provider.embed_texts(["b", "a"])
        third = await provider.embed_texts(["a", "new"])
    assert raw.calls == [
        (["a", "b"], EmbeddingPurpose.DOCUMENT),
        (["new"], EmbeddingPurpose.DOCUMENT),
    ]
    assert first.vectors == [[1.0, 1.0]] * 3
    assert second.billed_input_tokens == 0
    assert third.billed_input_tokens == 10
    assert current_provider_scope() is None


@pytest.mark.parametrize(
    "change", ["purpose", "project", "version", "endpoint", "model", "dimensions", "adapter"]
)
async def test_cache_identity_separates_incompatible_work(change):
    raw = Embeddings()
    provider = CachedEmbeddingProvider(raw)
    scope = work()
    with attached_provider_scope(scope):
        await provider.embed_texts(["a"])
    next_scope = scope
    purpose = EmbeddingPurpose.DOCUMENT
    if change == "purpose":
        purpose = EmbeddingPurpose.QUERY
    elif change == "project":
        next_scope = replace(scope, project_id=uuid.uuid4())
    elif change == "version":
        next_scope = replace(scope, embedding_set_version=4)
    elif change == "endpoint":
        raw.cache_namespace = "https://other.example"
    elif change == "model":
        raw.model_name = "other-model"
    elif change == "dimensions":
        # Changing advertised dimensions while returning two values is rejected, never cached.
        raw.dimensions = 3
    else:
        raw.provider_version = "v2"
    with attached_provider_scope(next_scope):
        if change == "dimensions":
            with pytest.raises(ProviderError):
                await provider.embed_texts(["a"], purpose=purpose)
        else:
            await provider.embed_texts(["a"], purpose=purpose)
    assert len(raw.calls) == 2


async def test_concurrent_turns_pay_once_for_identical_passages():
    raw = Embeddings()
    provider = CachedEmbeddingProvider(raw)
    with attached_provider_scope(work()):
        results = await asyncio.gather(*(provider.embed_texts(["shared"]) for _ in range(6)))
    assert len(raw.calls) == 1
    assert all(result.vectors == results[0].vectors for result in results)


async def test_partial_build_failure_commits_completed_batches_for_retry():
    raw = Embeddings()
    raw.fail_call = 2
    provider = CachedEmbeddingProvider(raw)
    texts = [str(n) for n in range(97)]
    with attached_provider_scope(work()):
        with pytest.raises(ProviderError):
            await provider.embed_texts(texts)
        raw.fail_call = None
        result = await provider.embed_texts(texts)
    assert [len(call[0]) for call in raw.calls] == [96, 1, 1]
    assert len(result.vectors) == 97
    assert result.billed_input_tokens == 10


@pytest.mark.parametrize(
    "endpoint,payload,billed,cost",
    [
        (
            "/v2/embed",
            {"model": "embed-v4.0", "texts": ["abc"], "input_type": "search_document"},
            {"input_tokens": 1000},
            120,
        ),
        (
            "/v2/rerank",
            {"model": "rerank-v4.0-pro", "query": "q", "documents": ["doc"]},
            {"search_units": 2},
            5000,
        ),
    ],
)
async def test_meter_uses_actual_billed_units(endpoint, payload, billed, cost):
    store = AsyncMock()
    attempt = uuid.uuid4()
    store.reserve.return_value = attempt
    response = httpx.Response(200, json={"meta": {"billed_units": billed}})
    send = AsyncMock(return_value=response)
    with attached_provider_scope(work(store)):
        assert await metered_cohere_post(send, endpoint, payload) is response
    store.reserve.assert_awaited_once()
    store.complete.assert_awaited_once_with(
        attempt, "completed", billed.get("input_tokens"), billed.get("search_units"), cost
    )


async def test_budget_rejection_never_dispatches():
    store = AsyncMock()
    store.reserve.side_effect = ProviderBudgetError("exhausted", provider_name="cohere")
    send = AsyncMock()
    with (
        attached_provider_scope(work(store, enforce_budgets=True)),
        pytest.raises(ProviderBudgetError),
    ):
        await metered_cohere_post(send, "/v2/embed", {"model": "embed-v4.0", "texts": ["x"]})
    send.assert_not_awaited()
    store.complete.assert_not_awaited()


@pytest.mark.parametrize("failure", [httpx.ReadTimeout("uncertain"), asyncio.CancelledError()])
async def test_uncertain_attempts_retain_reservation_even_on_cancellation(failure):
    store = AsyncMock()
    attempt = uuid.uuid4()
    store.reserve.return_value = attempt
    with attached_provider_scope(work(store)), pytest.raises(type(failure)):
        await metered_cohere_post(
            AsyncMock(side_effect=failure), "/v2/embed", {"model": "embed-v4.0", "texts": ["x"]}
        )
    store.complete.assert_awaited_once_with(attempt, "unknown", None, None, None)


async def test_paid_evaluation_requires_opt_in_before_reservation():
    store = AsyncMock()
    send = AsyncMock()
    with (
        attached_provider_scope(replace(work(store), workload="evaluation")),
        pytest.raises(ProviderError, match="opt-in"),
    ):
        await metered_cohere_post(send, "/v2/embed", {"model": "embed-v4.0", "texts": ["x"]})
    store.reserve.assert_not_awaited()
    send.assert_not_awaited()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"meta": {"billed_units": {"input_tokens": True}}},
        {"meta": {"billed_units": {"input_tokens": -1}}},
    ],
)
def test_unknown_billing_is_not_zero(payload):
    assert billed_usage(payload) == (None, None)


@pytest.mark.parametrize("amount", [float("inf"), float("nan"), 0, -1])
def test_budgets_must_be_finite_positive(amount):
    with pytest.raises(ValidationError):
        ProviderCostsConfig(monthly_budget_usd=amount)


async def test_ordinary_suite_cannot_send_external_http():
    async with httpx.AsyncClient() as client:
        with pytest.raises(AssertionError, match="Ordinary tests"):
            await client.get("https://api.cohere.com/anything")


@pytest.mark.parametrize("invalid", ["nonfinite", "identity"])
async def test_invalid_provider_results_never_enter_durable_cache(invalid):
    raw = Embeddings()
    raw.embed_texts = AsyncMock(
        return_value=EmbeddingBatchResult(
            [[float("nan") if invalid == "nonfinite" else 1.0, 1.0]],
            "cohere",
            "wrong-model" if invalid == "identity" else "embed-v4.0",
            2,
            "v1",
            10,
        )
    )
    scope = work()
    with attached_provider_scope(scope), pytest.raises(ProviderError):
        await CachedEmbeddingProvider(raw).embed_texts(["text"])
    assert scope.store.data == {}


def test_custom_endpoint_namespace_never_stores_url_credentials():
    from app.platform.providers.implementations.cohere_embedding import CohereEmbeddingProvider

    provider = CohereEmbeddingProvider(
        api_key="fake",
        base_url="https://fake-user:fake-password@example.test/path?token=fake-secret",
    )
    assert provider.cache_namespace.startswith("sha256:")
    assert "fake" not in provider.cache_namespace
    assert "example" not in provider.cache_namespace


async def test_paid_evaluation_cannot_bypass_budgets_in_observation_mode():
    store = AsyncMock()
    send = AsyncMock()
    with (
        attached_provider_scope(
            replace(work(store, paid_evaluation_enabled=True), workload="evaluation")
        ),
        pytest.raises(ProviderError, match="enforced finite budgets"),
    ):
        await metered_cohere_post(send, "/v2/embed", {"model": "embed-v4.0", "texts": ["x"]})
    store.reserve.assert_not_awaited()
    send.assert_not_awaited()


async def test_completion_failure_keeps_successful_paid_batch_and_cache():
    store = MemoryStore()
    store.reserve = AsyncMock(return_value=uuid.uuid4())
    store.complete = AsyncMock(side_effect=TimeoutError("accounting unavailable"))
    scope = work(store)
    sends = AsyncMock(
        return_value=httpx.Response(200, json={"meta": {"billed_units": {"input_tokens": 5}}})
    )

    class MeteredEmbeddings(Embeddings):
        async def embed_texts(self, texts, *, purpose=EmbeddingPurpose.DOCUMENT):
            await metered_cohere_post(
                sends, "/v2/embed", {"model": self.model_name, "texts": texts}
            )
            return await super().embed_texts(texts, purpose=purpose)

    provider = CachedEmbeddingProvider(MeteredEmbeddings())
    with attached_provider_scope(scope):
        first = await provider.embed_texts(["paid text"])
        second = await provider.embed_texts(["paid text"])
    assert first.vectors == second.vectors
    sends.assert_awaited_once()
    store.reserve.assert_awaited_once()
    assert scope.counts["accounting_completion_failures"] == 1
    assert store.data


async def test_query_factory_binds_retained_and_active_versions_instead_of_scope_default(
    monkeypatch,
):
    from app.core.config import Settings
    from app.dependencies.retrieval import query_embedder_factory_for
    from app.modules.retrieval.embedding_identity import EmbeddingIdentity
    from app.platform.providers.implementations import embedding_factory

    raw = Embeddings()
    monkeypatch.setattr(embedding_factory, "_create_embedding_provider", lambda *a, **kw: raw)
    settings = Settings(provider_costs=ProviderCostsConfig(cache_enabled=True))
    scope = work()
    factory = query_embedder_factory_for(settings)
    with attached_provider_scope(scope):
        for version in (2, 4, 2):
            provider = factory(EmbeddingIdentity(version, "cohere", "embed-v4.0", 2, "manifest"))
            await provider.embed_texts(["same query"], purpose=EmbeddingPurpose.QUERY)
    assert len(raw.calls) == 2
    assert len(scope.store.data) == 2
    assert scope.embedding_set_version == 3


def test_sync_http_guard_blocks_external_provider_before_network():
    with httpx.Client(), pytest.raises(AssertionError, match="cannot contact external"):
        httpx.get("https://api.cohere.com/v2/embed")
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200))) as client:
        assert client.get("https://api.cohere.com").status_code == 200


@pytest.mark.parametrize("payload_version,snapshot_version", [(4, 2), (None, 2)])
@pytest.mark.parametrize("new_build", [False, True])
async def test_corpus_worker_cache_uses_resolved_payload_or_snapshot_version(
    monkeypatch, payload_version, snapshot_version, new_build
):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from app.core.config import Settings
    from app.models.index_build import IndexBuildOperation, IndexBuildState
    from app.models.job_run import JobType
    from app.platform.jobs.configuration import build_job_configuration
    from app.platform.providers.provider_work import cache_identity, cache_key
    from app.worker.handlers import corpus

    settings = Settings(provider_costs=ProviderCostsConfig(cache_enabled=True))
    snapshot_settings = settings.model_copy(
        update={
            "retrieval": settings.retrieval.model_copy(
                update={"embedding_set_version": snapshot_version}
            )
        }
    )
    snapshot = SimpleNamespace(
        configuration=build_job_configuration(snapshot_settings).model_dump(mode="json")
    )
    session = AsyncMock()
    session.get.return_value = snapshot
    scope = work()
    raw = Embeddings()
    build = SimpleNamespace(
        id=uuid.uuid4(),
        state=IndexBuildState.BUILDING,
        embedding_set_version=3,
        structural_contract_version=None,
        document_count=1,
        chunk_count=1,
        corpus_fingerprint="f" * 64,
    )
    repository = MagicMock()
    repository.get_by_id = AsyncMock(return_value=build)
    repository.flush = AsyncMock()

    def add_created(value):
        nonlocal build
        build = value
        build.id = uuid.uuid4()
        build.document_count = build.chunk_count = 1
        build.corpus_fingerprint = "f" * 64

    repository.add.side_effect = add_created
    monkeypatch.setattr(corpus, "IndexBuildRepository", lambda *a: repository)
    monkeypatch.setattr(
        corpus, "create_embedding_provider", lambda s, **kw: CachedEmbeddingProvider(raw, **kw)
    )

    class Workflow:
        def __init__(self, **kw):
            self.embedder = kw["embedder"]

        async def run(self, *a, **kw):
            await self.embedder.embed_texts(["worker document"])
            return build

    monkeypatch.setattr(corpus, "IndexBuildWorkflow", Workflow)
    run = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=scope.project_id,
        job_type=JobType.CORPUS_REEMBED,
        configuration_snapshot_id=uuid.uuid4(),
        payload={"build_id": str(build.id)},
    )
    if new_build:
        run.payload.pop("build_id")
    if payload_version:
        run.payload["embedding_set_version"] = payload_version
    with attached_provider_scope(scope):
        await corpus.execute_index_build(
            session,
            run,
            settings,
            MagicMock(),
            operation=IndexBuildOperation.REEMBED,
            auto_activate_default=False,
        )
    version = payload_version or snapshot_version
    expected_key = cache_key(
        cache_identity(raw, version, EmbeddingPurpose.DOCUMENT), "worker document"
    )
    assert set(scope.store.data) == {(scope.project_id, expected_key)}
    assert build.embedding_set_version == version


@pytest.mark.parametrize(
    "origin", [None, {"namespace": "sha256:source-endpoint", "embedding_set_version": 3}]
)
async def test_revalidation_manifest_preserves_source_origin_even_after_endpoint_change(
    monkeypatch, origin
):
    from unittest.mock import MagicMock

    from app.models.index_build import IndexBuild, IndexBuildOperation, IndexBuildState
    from app.modules.retrieval.workflows import index_build_workflow as module

    session = AsyncMock()
    project = uuid.uuid4()
    target = IndexBuild(
        id=uuid.uuid4(),
        project_id=project,
        operation=IndexBuildOperation.REINDEX,
        state=IndexBuildState.BUILDING,
        embedding_set_version=3,
        structural_contract_version="structure.v1",
    )
    source = IndexBuild(
        id=uuid.uuid4(),
        project_id=project,
        operation=IndexBuildOperation.REINDEX,
        state=IndexBuildState.RETAINED,
        embedding_set_version=3,
        structural_contract_version="structure.v1",
        manifest={"documents": [], "embedding_origin": origin},
    )
    raw = Embeddings()
    workflow = module.IndexBuildWorkflow(
        session,
        project,
        raw,
        embedding_set_version=3,
        batch_size=32,
        filterable_metadata_keys=[],
        fts_regconfig="simple",
        reuse_index_build_id=source.id,
        private_chunk_factory=AsyncMock(),
    )
    workflow._builds = MagicMock()
    workflow._builds.get_by_id = AsyncMock(side_effect=[target, source])
    workflow._clear_partial_rows = AsyncMock()
    workflow._eligible_documents = AsyncMock(return_value=[])
    workflow._rebuild_statistics = AsyncMock()
    workflow._validate_versions = AsyncMock()
    monkeypatch.setattr(module, "verify_structural_build", AsyncMock())
    monkeypatch.setattr(module, "verify_semantic_scope_snapshot", AsyncMock())
    result = await workflow.run(target.id)
    assert result.manifest["embedding_origin"] == origin
    assert source.manifest["embedding_origin"] == origin
    assert raw.calls == []
