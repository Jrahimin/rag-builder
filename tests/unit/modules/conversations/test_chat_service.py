"""Unit tests for ChatService."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import ChatConfig, EvidenceGateMode, LLMBackend, LLMConfig, RetrievalConfig
from app.core.exceptions import ConflictError, NotFoundError, ServiceUnavailableError
from app.models.conversation import Conversation
from app.models.message import Message, MessageRole
from app.modules.conversations.ports import ContextChunk, ContextRetrievalResult
from app.modules.conversations.schemas.message import MessageSendRequest
from app.modules.conversations.services.chat_service import ChatService
from app.platform.providers.contracts.llm import (
    ChatCompletionChunk,
    ChatCompletionResult,
    ChatUsage,
)
from app.platform.providers.errors import ProviderError
from app.platform.providers.implementations.echo_chat import EchoLLMProvider

pytestmark = pytest.mark.unit


class FakeRetrieval:
    async def retrieve(self, **kwargs: object) -> ContextRetrievalResult:
        del kwargs
        return ContextRetrievalResult(
            chunks=[
                ContextChunk(
                    chunk_id=uuid.uuid4(),
                    document_id=uuid.uuid4(),
                    chunk_index=0,
                    content="refund within 30 days",
                    score=0.9,
                    filename="policy.txt",
                    chunk_hash="hash1",
                    semantic_score=0.9,
                )
            ],
            diagnostics={"index_build_id": str(uuid.uuid4())},
        )


class EmptyRetrieval:
    async def retrieve(self, **kwargs: object) -> ContextRetrievalResult:
        del kwargs
        return ContextRetrievalResult(chunks=[], diagnostics={})


class NearMissRetrieval:
    """Relevant Bangla evidence whose whole-chunk cosine sits just under 0.35."""

    chunk_id = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")

    async def retrieve(self, **kwargs: object) -> ContextRetrievalResult:
        del kwargs
        return ContextRetrievalResult(
            chunks=[
                ContextChunk(
                    chunk_id=self.chunk_id,
                    document_id=uuid.uuid4(),
                    chunk_index=0,
                    content="সঞ্চয়পত্র হইতে অর্জিত মুনাফা সম্পত্তির অধিগ্রহণ রপ্তানির বিপরীতে",
                    score=0.018,
                    filename="gazette.pdf",
                    chunk_hash="gazette-table",
                    semantic_score=0.32,
                    rank_score=0.018,
                    metadata={
                        "rrf_contributions": [
                            {"family": "original_dense", "rank": 1, "rrf": 0.016},
                            {"family": "translated_dense", "rank": 2, "rrf": 0.002},
                        ]
                    },
                )
            ],
            diagnostics={"candidate_trace": [{"chunk_id": str(self.chunk_id), "rank": 1}]},
        )


class FailingLLM(EchoLLMProvider):
    async def generate(self, messages, *, temperature, max_tokens):
        del messages, temperature, max_tokens
        raise ProviderError("boom", provider_name="echo")


class FailingStreamingLLM(EchoLLMProvider):
    async def stream(self, messages, *, temperature, max_tokens):
        del messages, temperature, max_tokens
        yield ChatCompletionChunk(delta="partial ")
        raise ProviderError("boom", provider_name="echo")


class AltModelLLM(EchoLLMProvider):
    def __init__(self) -> None:
        super().__init__(model="alt-model", provider_version="1")


@pytest.fixture
def session() -> AsyncMock:
    mock = AsyncMock()
    mock.in_transaction = MagicMock(return_value=True)

    async def refresh_side_effect(entity: object) -> None:
        if getattr(entity, "id", None) is None:
            entity.id = uuid.uuid4()  # type: ignore[attr-defined]
        now = datetime.now(UTC)
        if getattr(entity, "created_at", None) is None:
            entity.created_at = now  # type: ignore[attr-defined]
        if getattr(entity, "updated_at", None) is None:
            entity.updated_at = now  # type: ignore[attr-defined]
        if getattr(entity, "message_metadata", None) is None:
            entity.message_metadata = {}  # type: ignore[attr-defined]
        if getattr(entity, "citations", None) is None:
            entity.citations = []  # type: ignore[attr-defined]

    mock.commit = AsyncMock()
    mock.rollback = AsyncMock()
    mock.refresh = AsyncMock(side_effect=refresh_side_effect)
    return mock


@pytest.fixture
def conversation() -> Conversation:
    return Conversation(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        title=None,
        provider="echo",
        model="test",
        temperature=0.5,
        system_prompt_version="v1",
        is_active=True,
        deleted_at=None,
        deleted_by=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.fixture
def conversation_repository(conversation: Conversation) -> AsyncMock:
    mock = AsyncMock()
    mock.get_by_id = AsyncMock(return_value=conversation)
    mock.flush = AsyncMock()
    return mock


@pytest.fixture
def message_repository() -> AsyncMock:
    mock = AsyncMock()
    mock.add = MagicMock(side_effect=lambda entity: entity)
    mock.flush = AsyncMock()
    mock.list_recent_for_conversation = AsyncMock(return_value=[])
    return mock


def _service(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    llm: EchoLLMProvider,
    *,
    chat_config: ChatConfig | None = None,
) -> ChatService:
    return ChatService(
        session=session,
        project_id=uuid.uuid4(),
        conversation_repository=conversation_repository,
        message_repository=message_repository,
        retrieval=FakeRetrieval(),
        chat_config=chat_config or ChatConfig(system_prompt_version="v1"),
        retrieval_config=RetrievalConfig(),
        llm_config=LLMConfig(backend=LLMBackend.ECHO, max_tokens=100, temperature=0.2),
        resolve_llm=lambda _conversation: llm,
    )


async def test_zero_history_limit_excludes_prior_messages(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    captured_contents: list[str] = []

    class CapturingLLM(EchoLLMProvider):
        async def generate(self, messages, *, temperature, max_tokens):
            captured_contents.extend(message.content for message in messages)
            return await super().generate(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

    prior = Message(
        id=uuid.uuid4(),
        project_id=conversation.project_id,
        conversation_id=conversation.id,
        role=MessageRole.USER,
        content="must not be included",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    message_repository.list_recent_for_conversation.return_value = [prior]
    service = _service(
        session,
        conversation_repository,
        message_repository,
        CapturingLLM(model="test", provider_version="1"),
        chat_config=ChatConfig(system_prompt_version="v1", max_history_messages=0),
    )

    await service.send_message(
        conversation.id,
        MessageSendRequest(content="current question"),
    )

    assert "must not be included" not in captured_contents
    assert captured_contents[-1] == "current question"


async def test_send_message_commits_user_before_assistant(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
) -> None:
    service = _service(
        session,
        conversation_repository,
        message_repository,
        EchoLLMProvider(model="test", provider_version="1"),
    )
    turn = await service.send_message(
        conversation_repository.get_by_id.return_value.id,
        MessageSendRequest(content="What is the refund policy?"),
    )
    assert session.commit.await_count == 2
    assert session.rollback.await_count >= 1
    assert turn.user_message.content == "What is the refund policy?"
    assert turn.assistant_message.content.startswith("[echo]")
    assert turn.assistant_message.citations
    assert conversation_repository.get_by_id.return_value.title is not None


async def test_send_message_llm_failure_persists_failed_execution(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
) -> None:
    service = _service(
        session,
        conversation_repository,
        message_repository,
        FailingLLM(model="test", provider_version="1"),
    )
    with pytest.raises(ServiceUnavailableError, match="temporarily unavailable"):
        await service.send_message(
            conversation_repository.get_by_id.return_value.id,
            MessageSendRequest(content="question"),
        )
    assert session.commit.await_count == 2
    assistant = message_repository.add.call_args_list[-1].args[0]
    assert assistant.role is MessageRole.ASSISTANT
    assert assistant.finish_reason == "error"
    assert assistant.input_tokens is None
    assert assistant.output_tokens is None
    assert assistant.message_metadata["execution_status"] == "failed"
    assert assistant.message_metadata["execution_error_code"] == "provider_error"


async def test_stream_failure_persists_partial_failed_execution(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
) -> None:
    service = _service(
        session,
        conversation_repository,
        message_repository,
        FailingStreamingLLM(model="test", provider_version="1"),
    )

    with pytest.raises(ServiceUnavailableError, match="temporarily unavailable"):
        async for _ in service.stream_message(
            conversation_repository.get_by_id.return_value.id,
            MessageSendRequest(content="question"),
        ):
            pass

    assert session.commit.await_count == 2
    assistant = message_repository.add.call_args_list[-1].args[0]
    assert assistant.content == "partial "
    assert assistant.finish_reason == "error"
    assert assistant.message_metadata["execution_status"] == "failed"


async def test_insufficient_evidence_skips_generation_and_persists_refusal(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    class CountingLLM(EchoLLMProvider):
        calls = 0

        async def generate(self, messages, *, temperature, max_tokens):
            self.calls += 1
            return await super().generate(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

    llm = CountingLLM(model="test", provider_version="1")
    service = _service(session, conversation_repository, message_repository, llm)
    service._retrieval = EmptyRetrieval()

    turn = await service.send_message(
        conversation.id,
        MessageSendRequest(content="What is the lunar payroll rule?"),
    )

    assert llm.calls == 0
    assert turn.assistant_message.finish_reason == "insufficient_evidence"
    assert turn.assistant_message.insufficient_evidence_reason == "no_retrieval_results"
    assert turn.assistant_message.grounded is False
    assert turn.assistant_message.claims == []
    assert turn.assistant_message.citations == []
    gate = turn.assistant_message.metadata["evidence_gate"]
    assert gate["mode"] == "enforce"
    assert gate["generation_ran"] is False
    assert gate["blocked_generation"] is True


async def test_observe_mode_generates_when_cosine_gate_would_refuse(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    class CountingLLM(EchoLLMProvider):
        calls = 0

        async def generate(self, messages, *, temperature, max_tokens):
            self.calls += 1
            return await super().generate(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

    llm = CountingLLM(model="test", provider_version="1")
    service = _service(
        session,
        conversation_repository,
        message_repository,
        llm,
        chat_config=ChatConfig(
            system_prompt_version="v1",
            evidence_gate_mode=EvidenceGateMode.OBSERVE,
        ),
    )
    service._retrieval = NearMissRetrieval()

    turn = await service.send_message(
        conversation.id,
        MessageSendRequest(content="what are the source tax deduction areas?"),
    )

    assert llm.calls == 1
    assert turn.assistant_message.finish_reason != "insufficient_evidence"
    assert turn.assistant_message.insufficient_evidence_reason is None
    assert turn.assistant_message.citations
    gate = turn.assistant_message.metadata["evidence_gate"]
    assert gate["mode"] == "observe"
    assert gate["sufficient"] is False
    assert gate["reason"] == "below_relevance_threshold"
    assert gate["generation_ran"] is True
    assert gate["blocked_generation"] is False
    assert gate["evidence_score"] == pytest.approx(0.32)
    assert gate["winning_semantic_score"] == pytest.approx(0.32)
    assert gate["winning_rank_score"] == pytest.approx(0.018)
    assert gate["winning_chunk_id"] == str(NearMissRetrieval.chunk_id)


async def test_observe_mode_still_skips_generation_when_retrieval_is_empty(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    class CountingLLM(EchoLLMProvider):
        calls = 0

        async def generate(self, messages, *, temperature, max_tokens):
            self.calls += 1
            return await super().generate(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

    llm = CountingLLM(model="test", provider_version="1")
    service = _service(
        session,
        conversation_repository,
        message_repository,
        llm,
        chat_config=ChatConfig(
            system_prompt_version="v1",
            evidence_gate_mode=EvidenceGateMode.OBSERVE,
        ),
    )
    service._retrieval = EmptyRetrieval()

    turn = await service.send_message(
        conversation.id,
        MessageSendRequest(content="What is the lunar payroll rule?"),
    )

    assert llm.calls == 0
    assert turn.assistant_message.finish_reason == "insufficient_evidence"
    assert turn.assistant_message.insufficient_evidence_reason == "no_retrieval_results"
    assert turn.assistant_message.metadata["evidence_gate"]["generation_ran"] is False


async def test_enforce_mode_still_skips_generation_on_a_cosine_near_miss(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    class CountingLLM(EchoLLMProvider):
        calls = 0

        async def generate(self, messages, *, temperature, max_tokens):
            self.calls += 1
            return await super().generate(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

    llm = CountingLLM(model="test", provider_version="1")
    service = _service(session, conversation_repository, message_repository, llm)
    service._retrieval = NearMissRetrieval()

    turn = await service.send_message(
        conversation.id,
        MessageSendRequest(content="what are the source tax deduction areas?"),
    )

    assert llm.calls == 0
    assert turn.assistant_message.finish_reason == "insufficient_evidence"
    assert turn.assistant_message.insufficient_evidence_reason == "below_relevance_threshold"
    gate = turn.assistant_message.metadata["evidence_gate"]
    assert gate["mode"] == "enforce"
    assert gate["sufficient"] is False
    assert gate["generation_ran"] is False
    assert gate["evidence_score"] == pytest.approx(0.32)


async def test_applied_rerank_above_threshold_runs_generation(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    class CountingLLM(EchoLLMProvider):
        calls = 0

        async def generate(self, messages, *, temperature, max_tokens):
            self.calls += 1
            return await super().generate(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

    class AppliedRerankRetrieval:
        async def retrieve(self, **kwargs: object) -> ContextRetrievalResult:
            del kwargs
            return ContextRetrievalResult(
                chunks=[
                    ContextChunk(
                        chunk_id=uuid.uuid4(),
                        document_id=uuid.uuid4(),
                        chunk_index=2,
                        content="উৎসে কর সংগ্রহের খাত সঞ্চয়পত্র হইতে অর্জিত মুনাফা",
                        score=0.8693157,
                        filename="gazette.pdf",
                        chunk_hash="gazette-table",
                        semantic_score=0.323,
                        rank_score=None,
                        rerank_relevance_score=None,
                        metadata={"rerank_status": "applied"},
                    )
                ],
                diagnostics={"rerank_status": "applied"},
            )

    llm = CountingLLM(model="test", provider_version="1")
    service = _service(session, conversation_repository, message_repository, llm)
    service._retrieval = AppliedRerankRetrieval()

    turn = await service.send_message(
        conversation.id,
        MessageSendRequest(content="উৎসে কর সংগ্রহের খাত কি?"),
    )

    assert llm.calls == 1
    assert turn.assistant_message.finish_reason != "insufficient_evidence"
    gate = turn.assistant_message.metadata["evidence_gate"]
    assert gate["sufficient"] is True
    assert gate["evidence_score"] == pytest.approx(0.8693157)
    assert gate["evidence_score_method"] == "reranker_relevance"
    selected = turn.assistant_message.metadata["retrieval_trace"]["context_selected"]
    assert selected[0]["rerank_relevance_score"] == pytest.approx(0.8693157)


async def test_send_message_uses_conversation_temperature(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    captured: dict[str, float] = {}

    class CapturingLLM(EchoLLMProvider):
        async def generate(self, messages, *, temperature, max_tokens):
            captured["temperature"] = temperature
            return await super().generate(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

    conversation.temperature = 0.5
    service = _service(
        session,
        conversation_repository,
        message_repository,
        CapturingLLM(model="test", provider_version="1"),
    )
    await service.send_message(
        conversation.id,
        MessageSendRequest(content="hello"),
    )
    assert captured["temperature"] == 0.5


async def test_resolve_llm_uses_conversation_model(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    conversation.model = "alt-model"
    service = _service(
        session,
        conversation_repository,
        message_repository,
        AltModelLLM(),
    )
    turn = await service.send_message(
        conversation.id,
        MessageSendRequest(content="hello"),
    )
    assert turn.assistant_message.model == "alt-model"


async def test_releases_read_transaction_before_generation(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
) -> None:
    service = _service(
        session,
        conversation_repository,
        message_repository,
        EchoLLMProvider(model="test", provider_version="1"),
    )
    await service.send_message(
        conversation_repository.get_by_id.return_value.id,
        MessageSendRequest(content="hello"),
    )
    session.rollback.assert_awaited()
    assert session.commit.await_count == 2


async def test_deleted_conversation_rejected(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    conversation.deleted_at = datetime.now(UTC)
    service = _service(
        session,
        conversation_repository,
        message_repository,
        EchoLLMProvider(model="test", provider_version="1"),
    )
    with pytest.raises(ConflictError, match="Cannot modify a deleted conversation"):
        await service.send_message(
            conversation.id,
            MessageSendRequest(content="hello"),
        )


async def test_inactive_conversation_rejected(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    conversation.is_active = False
    service = _service(
        session,
        conversation_repository,
        message_repository,
        EchoLLMProvider(model="test", provider_version="1"),
    )
    with pytest.raises(NotFoundError, match="not active"):
        await service.send_message(
            conversation.id,
            MessageSendRequest(content="hello"),
        )


async def test_stream_message_yields_done_event(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    service = _service(
        session,
        conversation_repository,
        message_repository,
        EchoLLMProvider(model="test", provider_version="1"),
    )
    events: list[str | dict] = []
    async for item in service.stream_message(
        conversation.id,
        MessageSendRequest(content="stream me"),
    ):
        events.append(item)
    assert any(isinstance(item, str) for item in events)
    done = next(item for item in events if isinstance(item, dict))
    assert done["event"] == "done"
    assert done["assistant_message_id"]
    assistant = message_repository.add.call_args_list[-1].args[0]
    assert assistant.input_tokens is not None
    assert assistant.output_tokens is not None
    assert assistant.provider_latency_ms is not None
    assert assistant.total_latency_ms is not None


async def test_stream_cancel_skips_assistant_persist(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    service = _service(
        session,
        conversation_repository,
        message_repository,
        EchoLLMProvider(model="test", provider_version="1"),
    )
    cancel_after_first = False

    async def should_cancel() -> bool:
        return cancel_after_first

    events: list[str | dict] = []
    async for item in service.stream_message(
        conversation.id,
        MessageSendRequest(content="one two three"),
        should_cancel=should_cancel,
    ):
        if isinstance(item, str):
            cancel_after_first = True
        events.append(item)
    assert session.commit.await_count == 1
    assert not any(isinstance(item, dict) for item in events)


async def test_applied_rerank_without_corroboration_blocks_unrelated_query(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    class CountingLLM(EchoLLMProvider):
        calls = 0

        async def generate(self, messages, *, temperature, max_tokens):
            self.calls += 1
            return await super().generate(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

    class UnrelatedRerankRetrieval:
        async def retrieve(self, **kwargs: object) -> ContextRetrievalResult:
            del kwargs
            return ContextRetrievalResult(
                chunks=[
                    ContextChunk(
                        chunk_id=uuid.uuid4(),
                        document_id=uuid.uuid4(),
                        chunk_index=2,
                        content="উৎসে কর সংগ্রহের খাত সঞ্চয়পত্র হইতে অর্জিত মুনাফা",
                        score=0.61,
                        filename="gazette.pdf",
                        chunk_hash="gazette-table",
                        semantic_score=0.18,
                        rerank_relevance_score=0.61,
                        metadata={"rerank_status": "applied"},
                    )
                ],
                diagnostics={"rerank_status": "applied"},
            )

    llm = CountingLLM(model="test", provider_version="1")
    service = _service(session, conversation_repository, message_repository, llm)
    service._retrieval = UnrelatedRerankRetrieval()

    turn = await service.send_message(
        conversation.id,
        MessageSendRequest(content="What is the maternity leave policy?"),
    )

    assert llm.calls == 0
    assert turn.assistant_message.finish_reason == "insufficient_evidence"
    gate = turn.assistant_message.metadata["evidence_gate"]
    assert gate["sufficient"] is False
    assert gate["evidence_score"] == pytest.approx(0.61)
    assert gate["winning_semantic_score"] == pytest.approx(0.18)


async def test_cited_english_answer_is_grounded_when_query_embedder_confirms(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    from app.platform.providers.contracts.embedding import (
        BaseEmbeddingProvider,
        EmbeddingBatchResult,
        EmbeddingPurpose,
    )

    table = "সঞ্চয়পত্র হইতে অর্জিত মুনাফা সম্পত্তির অধিগ্রহণ রপ্তানির বিপরীতে মোটরযান"
    claim = "Source tax categories include savings certificates and property acquisition."

    class _ClusterEmbedder(BaseEmbeddingProvider):
        @property
        def provider_name(self) -> str:
            return "test"

        @property
        def model_name(self) -> str:
            return "cluster"

        @property
        def dimensions(self) -> int:
            return 4

        @property
        def provider_version(self) -> str:
            return "1"

        async def embed_texts(
            self,
            texts: list[str],
            *,
            purpose: EmbeddingPurpose = EmbeddingPurpose.DOCUMENT,
        ) -> EmbeddingBatchResult:
            del purpose
            vector = [1.0, 0.0, 0.0, 0.0]
            return EmbeddingBatchResult(
                vectors=[vector for _ in texts],
                provider=self.provider_name,
                model=self.model_name,
                dimensions=self.dimensions,
                provider_version=self.provider_version,
            )

    class CitedLLM(EchoLLMProvider):
        async def generate(self, messages, *, temperature, max_tokens):
            del messages, temperature, max_tokens
            return ChatCompletionResult(
                content=f"{claim} [1]",
                provider="echo",
                model="test",
                finish_reason="stop",
                usage=ChatUsage(1, 8),
                provider_version="1",
            )

    class GazetteRetrieval:
        query_embedder = _ClusterEmbedder()

        async def retrieve(self, **kwargs: object) -> ContextRetrievalResult:
            del kwargs
            return ContextRetrievalResult(
                chunks=[
                    ContextChunk(
                        chunk_id=uuid.uuid4(),
                        document_id=uuid.uuid4(),
                        chunk_index=2,
                        content=table,
                        score=0.869,
                        filename="gazette.pdf",
                        chunk_hash="gazette-table",
                        semantic_score=0.323,
                        rerank_relevance_score=0.869,
                        metadata={"rerank_status": "applied"},
                    )
                ],
                diagnostics={"rerank_status": "applied"},
            )

    service = _service(
        session,
        conversation_repository,
        message_repository,
        CitedLLM(model="test", provider_version="1"),
    )
    service._retrieval = GazetteRetrieval()

    turn = await service.send_message(
        conversation.id,
        MessageSendRequest(content="what are the source tax deduction areas?"),
    )

    assert turn.assistant_message.finish_reason != "insufficient_evidence"
    assert turn.assistant_message.grounded is True
    assert turn.assistant_message.metadata["grounded"] is True
    assert turn.assistant_message.metadata["citation_coverage"] == 1.0
    assert turn.assistant_message.claims[0].verification == "supported"
