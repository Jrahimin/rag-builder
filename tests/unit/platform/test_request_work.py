"""Turn reuse must not mix projects, identities, purposes, or cancelled results."""

import asyncio
import uuid
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from app.platform.providers.contracts.embedding import EmbeddingPurpose
from app.platform.providers.errors import ProviderError
from app.platform.providers.implementations.hash_embedding import HashEmbeddingProvider
from app.platform.providers.request_work import RequestWork


@pytest.mark.asyncio
async def test_prompt_budget_rejects_before_provider_call_and_reports_only_counts():
    from app.platform.providers.contracts.llm import ChatMessage, ChatRole
    from app.platform.providers.implementations.echo_chat import EchoLLMProvider
    from app.platform.providers.request_work import ObservedLLM

    provider = EchoLLMProvider(model="unknown-tokenizer", provider_version="1")
    provider.generate = AsyncMock()
    work = RequestWork(uuid.uuid4())
    observed = ObservedLLM(provider, work, capacity=1024)
    with pytest.raises(ProviderError):
        await observed.generate(
            [ChatMessage(role=ChatRole.USER, content="বাংলা" * 100)], max_tokens=128
        )
    provider.generate.assert_not_awaited()
    assert work.counts["prompt_budget_rejections"] == 1
    assert work.calls[0]["count_method"] == "utf8_byte_upper_bound"
    assert "বাংলা" not in str(work.snapshot())


def test_hosted_model_capacity_preserves_large_bangla_coverage_requests():
    from app.core.config import LLMConfig
    from app.platform.providers.contracts.llm import ChatMessage, ChatRole
    from app.platform.providers.prompt_budget import prompt_budget

    config = LLMConfig()
    budget = prompt_budget(
        [ChatMessage(role=ChatRole.SYSTEM, content="বাংলা" * 12_000)],
        model="gpt-5.6-luna",
        capacity=config.model_context_windows["gpt-5.6-luna"],
        reserved_output=4096,
    )
    assert budget["within_budget"]
    assert (
        LLMConfig(model_context_windows={"gpt-5.6-luna": 32768}).model_context_windows[
            "gpt-5.6-luna"
        ]
        == 32768
    )


@pytest.mark.asyncio
async def test_coalesces_exact_concurrent_texts_but_separates_purpose_and_project():
    provider = HashEmbeddingProvider(dimensions=8, model="hash", provider_version="1")
    original = provider.embed_texts
    provider.embed_texts = AsyncMock(side_effect=original)
    work = RequestWork(uuid.uuid4())
    cached = work.wrap(provider)
    a, b = await asyncio.gather(cached.embed_texts(["same", "same"]), cached.embed_texts(["same"]))
    assert a.vectors[0] == b.vectors[0]
    assert provider.embed_texts.await_count == 1
    await cached.embed_texts(["same"], purpose=EmbeddingPurpose.QUERY)
    await cached.embed_texts(["same "])
    await RequestWork(uuid.uuid4()).wrap(provider).embed_texts(["same"])
    assert provider.embed_texts.await_count == 4
    assert work.snapshot()["counts"]["embedding_cache_hits"] == 2
    assert "same" not in str(work.snapshot())


@pytest.mark.asyncio
async def test_bad_identity_and_cancellation_never_poison_cache():
    provider = HashEmbeddingProvider(dimensions=8, model="hash", provider_version="1")
    original = await provider.embed_texts(["text"])
    provider.embed_texts = AsyncMock(return_value=replace(original, model="wrong-model"))
    work = RequestWork(uuid.uuid4())
    cached = work.wrap(provider)
    with pytest.raises(ProviderError):
        await cached.embed_texts(["text"])
    assert not work.vectors
    entered = asyncio.Event()

    async def blocked(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    provider.embed_texts = blocked
    task = asyncio.create_task(cached.embed_texts(["text"]))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not work.vectors and not work.embedding_lock.locked()


@pytest.mark.asyncio
async def test_model_dimensions_and_provider_version_are_distinct():
    work = RequestWork(uuid.uuid4())
    small = work.wrap(HashEmbeddingProvider(dimensions=8, model="hash", provider_version="1"))
    large = work.wrap(HashEmbeddingProvider(dimensions=16, model="hash", provider_version="1"))
    assert len((await small.embed_texts(["text"])).vectors[0]) == 8
    assert len((await large.embed_texts(["text"])).vectors[0]) == 16
    assert work.counts["embedding_calls"] == 2
