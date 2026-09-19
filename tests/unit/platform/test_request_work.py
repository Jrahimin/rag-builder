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


@pytest.mark.asyncio
async def test_nested_and_parallel_spans_keep_task_local_parents():
    work = RequestWork(uuid.uuid4())
    with work.stage("parent"):
        with work.stage("child"):
            pass

        async def branch(name: str) -> None:
            with work.stage(name):
                await asyncio.sleep(0.04)

        await asyncio.gather(branch("left"), branch("right"))
    snapshot = work.snapshot()
    items = snapshot["spans"]["items"]
    by_name = {span["name"]: span for span in items}
    parent_id = by_name["parent"]["id"]
    assert by_name["child"]["parent_id"] == parent_id
    assert by_name["left"]["parent_id"] == parent_id
    assert by_name["right"]["parent_id"] == parent_id
    assert by_name["left"]["id"] != by_name["right"]["id"]
    assert snapshot["processing_ms"] <= (
        by_name["left"]["elapsed_ms"] + by_name["right"]["elapsed_ms"]
    )
    assert snapshot["spans"]["overlap"]


@pytest.mark.asyncio
async def test_cancelled_and_failed_spans_do_not_store_raw_exceptions():
    work = RequestWork(uuid.uuid4())
    with pytest.raises(ProviderError), work.stage("answer_generation"):
        raise ProviderError("sk-secret-token must not persist", provider_name="test")
    failed = work.snapshot()["spans"]["items"][0]
    assert failed["outcome"] == "failed"
    assert failed["error_category"] == "provider_error"
    assert "sk-secret" not in str(work.snapshot())

    started = asyncio.Event()

    async def blocked() -> None:
        with work.stage("blocked"):
            started.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(blocked())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    cancelled = next(
        span for span in work.snapshot()["spans"]["items"] if span["name"] == "blocked"
    )
    assert cancelled["outcome"] == "cancelled"
    assert "error_category" not in cancelled


@pytest.mark.asyncio
async def test_stage_reset_from_another_context_does_not_raise():
    import contextvars

    work = RequestWork(uuid.uuid4())

    async def stream():
        with work.stage("answer_generation"):
            yield "token"
            yield "done"

    agen = stream()
    assert await agen.__anext__() == "token"

    async def close_elsewhere() -> None:
        await agen.aclose()

    await asyncio.create_task(close_elsewhere(), context=contextvars.Context())
    items = work.snapshot()["spans"]["items"]
    assert items
    assert items[0]["name"] == "answer_generation"


@pytest.mark.asyncio
async def test_provider_calls_inherit_purpose_and_keep_unknown_retries():
    from app.platform.providers.contracts.llm import ChatMessage, ChatRole
    from app.platform.providers.implementations.echo_chat import EchoLLMProvider
    from app.platform.providers.request_work import ObservedLLM

    work = RequestWork(uuid.uuid4())
    observed = ObservedLLM(EchoLLMProvider(model="echo", provider_version="1"), work)
    with work.stage("turn_resolution"):
        await observed.generate([ChatMessage(role=ChatRole.USER, content="hello")], max_tokens=16)
    llm_call = work.calls[-1]
    assert llm_call["purpose"] == "turn_resolution"
    assert llm_call["provider_internal_retries"] is None
    assert llm_call["span_id"]

    cached = work.wrap(HashEmbeddingProvider(dimensions=8, model="hash", provider_version="1"))
    with work.stage("claim_verification"):
        await cached.embed_texts(["claim"], purpose=EmbeddingPurpose.QUERY)
    embedding = next(call for call in reversed(work.calls) if call["kind"] == "embedding")
    assert embedding["purpose"] == "query"
    assert embedding["work_purpose"] == "claim_verification"
    assert embedding["provider_internal_retries"] is None


def test_span_detail_is_bounded_while_aggregate_counts_remain():
    work = RequestWork(uuid.uuid4(), max_span_detail=3)
    for index in range(5):
        with work.stage(f"stage-{index}"):
            pass
    snapshot = work.snapshot()
    assert snapshot["version"] == "turn.v1"
    assert len(snapshot["spans"]["items"]) == 3
    assert snapshot["spans"]["omitted"] == 2
    assert snapshot["counts"]["spans"] == 5
    assert snapshot["counts"]["spans_omitted"] == 2
    assert snapshot["snapshot_includes_persistence"] is False


def test_persisted_snapshot_excludes_persistence_until_it_is_recorded():
    work = RequestWork(uuid.uuid4())
    persisted = work.snapshot()
    assert persisted["snapshot_includes_persistence"] is False
    assert "persistence" not in persisted["stages_ms"]
    work.timings["persistence"] += 15
    logged = work.snapshot()
    assert logged["snapshot_includes_persistence"] is True
    assert logged["stages_ms"]["persistence"] == 15


@pytest.mark.asyncio
async def test_wait_span_ends_before_the_protected_operation():
    work = RequestWork(uuid.uuid4())
    limiter = asyncio.Semaphore(1)
    async with work.wait("recovery_batch_semaphore"):
        await limiter.acquire()
    await asyncio.sleep(0.05)
    limiter.release()
    wait = work.snapshot()["spans"]["items"][0]
    assert wait["name"] == "recovery_batch_semaphore"
    assert wait["outcome"] == "completed"
    assert wait["elapsed_ms"] < 40


@pytest.mark.asyncio
async def test_concurrent_provider_calls_keep_stable_indexes():
    from app.platform.providers.contracts.llm import ChatMessage, ChatRole
    from app.platform.providers.implementations.echo_chat import EchoLLMProvider
    from app.platform.providers.request_work import ObservedLLM

    work = RequestWork(uuid.uuid4())
    slow = EchoLLMProvider(model="echo", provider_version="1")
    fast = EchoLLMProvider(model="echo", provider_version="1")
    original_slow = slow.generate

    async def delayed(messages, *, temperature=None, max_tokens):
        await asyncio.sleep(0.05)
        return await original_slow(messages, temperature=temperature, max_tokens=max_tokens)

    async def immediate(messages, *, temperature=None, max_tokens):
        del messages, temperature, max_tokens
        from app.platform.providers.contracts.llm import ChatCompletionResult, ChatUsage

        return ChatCompletionResult("fast", "echo", "echo", "stop", ChatUsage(1, 1), "1")

    slow.generate = delayed  # type: ignore[method-assign]
    fast.generate = immediate  # type: ignore[method-assign]
    observed_slow = ObservedLLM(slow, work)
    observed_fast = ObservedLLM(fast, work)
    message = [ChatMessage(role=ChatRole.USER, content="hello")]
    with work.stage("answer_generation"):
        await asyncio.gather(
            observed_slow.generate(message, max_tokens=16),
            observed_fast.generate(message, max_tokens=16),
        )
    indexes = work.snapshot()["spans"]["items"][0]["provider_call_indexes"]
    assert indexes == [0, 1]
    span_id = work.snapshot()["spans"]["items"][0]["id"]
    assert {call.get("span_id") for call in work.calls} == {span_id}


@pytest.mark.asyncio
async def test_stage_reset_from_another_context_marks_cancelled_and_clears_origin():
    import contextvars

    from app.platform.providers.request_work import current_request_purpose, current_request_span_id

    work = RequestWork(uuid.uuid4())
    origin = contextvars.copy_context()

    async def stream():
        span = work.begin_span("answer_generation")
        try:
            yield "token"
            yield "done"
        except GeneratorExit:
            work.finish_span(span, outcome="cancelled")
            raise
        else:
            work.finish_span(span, outcome="completed")

    agen = stream()
    assert await agen.__anext__() == "token"

    async def close_elsewhere() -> None:
        await agen.aclose()

    await asyncio.create_task(close_elsewhere(), context=contextvars.Context())
    items = work.snapshot()["spans"]["items"]
    assert items[0]["name"] == "answer_generation"
    assert items[0]["outcome"] == "cancelled"
    assert origin.run(current_request_purpose) is None
    assert origin.run(current_request_span_id) is None


def test_request_work_does_not_bind_on_construction():
    from app.platform.providers.request_work import current_request_work

    work = RequestWork(uuid.uuid4())
    assert current_request_work() is None
    with work.attached():
        assert current_request_work() is work
    assert current_request_work() is None


def test_sequential_turns_keep_separate_context_bindings():
    from app.platform.providers.request_work import current_request_work

    first = RequestWork(uuid.uuid4())
    second = RequestWork(uuid.uuid4())
    with first.attached():
        assert current_request_work() is first
    with second.attached():
        assert current_request_work() is second
    assert current_request_work() is None


def test_provider_call_without_task_local_span_is_left_unbound():
    work = RequestWork(uuid.uuid4())
    with work.stage("parent"):
        pass
    call: dict[str, object] = {"kind": "llm"}
    work.annotate_provider_call(call)
    assert "span_id" not in call


def test_streaming_unbound_purpose_still_labels_calls():
    work = RequestWork(uuid.uuid4())
    span = work.begin_span("answer_generation")
    call: dict[str, object] = {"kind": "llm"}
    work.annotate_provider_call(call)
    assert call["purpose"] == "answer_generation"
    assert "span_id" not in call
    work.finish_span(span)


@pytest.mark.asyncio
async def test_stream_cancel_records_cancelled_provider_call():
    from app.platform.providers.contracts.llm import ChatMessage, ChatRole
    from app.platform.providers.implementations.echo_chat import EchoLLMProvider
    from app.platform.providers.request_work import ObservedLLM

    work = RequestWork(uuid.uuid4())
    observed = ObservedLLM(EchoLLMProvider(model="echo", provider_version="1"), work)
    agen = observed.stream(
        [ChatMessage(role=ChatRole.USER, content="hello")], max_tokens=16
    )
    assert await agen.__anext__()
    await agen.aclose()
    assert work.calls[0]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_snapshot_aggregates_tokens_and_calls_by_purpose():
    from app.platform.providers.contracts.llm import ChatMessage, ChatRole
    from app.platform.providers.implementations.echo_chat import EchoLLMProvider
    from app.platform.providers.request_work import ObservedLLM

    work = RequestWork(uuid.uuid4())
    observed = ObservedLLM(EchoLLMProvider(model="echo", provider_version="1"), work)
    with work.stage("turn_resolution"):
        await observed.generate(
            [ChatMessage(role=ChatRole.USER, content="hello")], max_tokens=16
        )
    snapshot = work.snapshot()
    assert snapshot["calls_by_purpose"]["turn_resolution"] == 1
    tokens = snapshot["tokens_by_purpose"]["turn_resolution"]
    assert tokens["input"] is None or tokens["input"] >= 0
    assert tokens["output"] is None or tokens["output"] >= 0
