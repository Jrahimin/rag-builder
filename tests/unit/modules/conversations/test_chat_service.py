"""Unit tests for ChatService."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import (
    ChatConfig,
    EvidenceGateMode,
    LLMBackend,
    LLMConfig,
    ResponseMode,
    RetrievalConfig,
)
from app.core.exceptions import ConflictError, NotFoundError, ServiceUnavailableError
from app.models.conversation import Conversation
from app.models.message import Message, MessageRole
from app.modules.conversations.ports import ContextChunk, ContextRetrievalResult
from app.modules.conversations.schemas.message import MessageSendRequest
from app.modules.conversations.services.chat_service import ChatService
from app.platform.domain.content_hash import content_hash
from app.platform.domain.evidence_contracts import (
    RERANKER_RELEVANCE_CALIBRATION_ID,
    BranchContribution,
    BranchScoreType,
    QueryVariant,
    QueryVariantKind,
)
from app.platform.providers.contracts.llm import (
    ChatCompletionChunk,
    ChatCompletionResult,
    ChatUsage,
)
from app.platform.providers.contracts.web_search import (
    WebDiscoveredSource,
    WebSearchEvidence,
    WebSearchResult,
)
from app.platform.providers.errors import ProviderError, ProviderTimeoutError
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


class CitedLLM(EchoLLMProvider):
    def __init__(self, content: str) -> None:
        super().__init__(model="test", provider_version="1")
        self.content = content

    async def generate(self, messages, *, temperature, max_tokens):
        del messages, temperature, max_tokens
        return ChatCompletionResult(
            content=self.content,
            provider="echo",
            model="test",
            finish_reason="stop",
            usage=ChatUsage(10, 5),
            provider_version="1",
        )

    async def stream(self, messages, *, temperature, max_tokens):
        del messages, temperature, max_tokens
        yield ChatCompletionChunk(delta=self.content)
        yield ChatCompletionChunk(
            delta="",
            finish_reason="stop",
            usage=ChatUsage(10, 5),
        )


class FakeWebSearch:
    def __init__(self, evidence: list[WebSearchEvidence] | None = None) -> None:
        self.calls: list[str] = []
        self.evidence = (
            evidence
            if evidence is not None
            else [
                WebSearchEvidence(
                    evidence_id="web-1",
                    title="Current refund guidance",
                    url="https://example.test/refunds",
                    content="Current web guidance allows refunds within 30 days.",
                    retrieved_at=datetime.now(UTC),
                    citation_verified=True,
                )
            ]
        )

    async def search(self, query: str, *, max_results: int) -> WebSearchResult:
        self.calls.append(query)
        evidence = self.evidence[:max_results]
        return WebSearchResult(
            evidence=evidence,
            provider="test_web",
            model="search-model",
            provider_version="1",
            diagnostics={
                "source_count": len(evidence),
            },
            discovered_sources=[
                WebDiscoveredSource(
                    provider_id=item.source_id,
                    title=item.title,
                    original_url=item.url,
                    canonical_url=item.canonical_url or item.url,
                )
                for item in evidence
            ],
        )


class FailingWebSearch:
    async def search(self, query: str, *, max_results: int) -> WebSearchResult:
        del query, max_results
        raise ProviderTimeoutError("timeout", provider_name="test_web")


class SourceOnlyWebSearch:
    async def search(self, query: str, *, max_results: int) -> WebSearchResult:
        del query, max_results
        return WebSearchResult(
            evidence=[],
            provider="test_web",
            model="search-model",
            provider_version="1",
            diagnostics={"source_count": 1},
            discovered_sources=[
                WebDiscoveredSource(
                    provider_id=None,
                    title="Policy",
                    original_url="https://example.test/policy",
                    canonical_url="https://example.test/policy",
                )
            ],
        )


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


async def test_candidate_wise_canary_sends_only_passing_lower_rank_to_generation(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    question = "What are the source tax deduction categories?"
    translated_text = "উৎসে কর কর্তনের খাতগুলো কী কী"
    relevant_text = "উৎসে কর কর্তনের খাতগুলো হলো সঞ্চয়পত্র এবং সম্পত্তি অধিগ্রহণ।"
    unrelated_text = "মাতৃত্বকালীন ছুটির আবেদন ব্যবস্থাপকের অনুমোদন সাপেক্ষ।"
    original = QueryVariant(
        variant_id="original",
        kind=QueryVariantKind.ORIGINAL,
        language="en",
        text=question,
    )
    translated = QueryVariant(
        variant_id="translated:bn",
        kind=QueryVariantKind.TRANSLATED,
        language="bn",
        text=translated_text,
        source_variant_id="original",
    )

    def candidate(content: str, score: float) -> ContextChunk:
        return ContextChunk(
            chunk_id=uuid.uuid4(),
            document_id=uuid.uuid4(),
            chunk_index=2,
            content=content,
            score=score,
            filename="sanitized.pdf",
            chunk_hash=content_hash(content),
            semantic_score=0.1,
            rerank_relevance_score=score,
            evidence_relevance_score=score,
            evidence_score_method="reranker_relevance",
            evidence_calibration_id=RERANKER_RELEVANCE_CALIBRATION_ID,
            query_variants=(original, translated),
            branch_contributions=(
                BranchContribution(
                    branch_id="translated_lexical:bn",
                    family="translated_lexical",
                    query_variant_id=translated.variant_id,
                    target_language="bn",
                    rank=1,
                    raw_score=8.0,
                    score_type=BranchScoreType.KEYWORD_BM25,
                    rrf_score=0.01,
                ),
            ),
            metadata={"rerank_status": "applied"},
        )

    relevant = candidate(relevant_text, 0.81)

    class CandidateRetrieval:
        async def retrieve(self, **kwargs: object) -> ContextRetrievalResult:
            del kwargs
            return ContextRetrievalResult(
                chunks=[candidate(unrelated_text, 0.92), relevant],
                diagnostics={"rerank_status": "applied"},
            )

    captured_system: list[str] = []

    class CapturingLLM(EchoLLMProvider):
        calls = 0

        async def generate(self, messages, *, temperature, max_tokens):
            del temperature, max_tokens
            self.calls += 1
            captured_system.append(messages[0].content)
            return ChatCompletionResult(
                content=f"{relevant_text} [1]",
                provider="echo",
                model="test",
                finish_reason="stop",
                usage=ChatUsage(10, 5),
                provider_version="1",
            )

    llm = CapturingLLM(model="test", provider_version="1")
    service = _service(
        session,
        conversation_repository,
        message_repository,
        llm,
        chat_config=ChatConfig(
            system_prompt_version="v5",
            candidate_wise_grounding_enabled=True,
        ),
    )
    service._retrieval = CandidateRetrieval()

    turn = await service.send_message(conversation.id, MessageSendRequest(content=question))

    assert llm.calls == 1
    assert relevant_text in captured_system[0]
    assert unrelated_text not in captured_system[0]
    assert len(turn.assistant_message.citations) == 1
    assert turn.assistant_message.citations[0].chunk_id == relevant.chunk_id
    candidate_diagnostics = turn.assistant_message.metadata["evidence_gate"]["candidate_wise"]
    assert candidate_diagnostics["assessed_count"] == 2
    assert candidate_diagnostics["admitted_count"] == 1
    assert candidate_diagnostics["retrieved_count"] == 2
    assert candidate_diagnostics["context_selected_count"] == 1
    assert candidate_diagnostics["cited_count"] == 1
    assert candidate_diagnostics["alerts"] == {
        "unknown_calibration_count": 0,
        "failed_span_derivation_count": 0,
        "missing_provenance_count": 0,
        "span_hash_mismatch_count": 0,
    }


async def test_candidate_wise_canary_refuses_when_admitted_unit_exceeds_context_budget(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    question = "What is the refund policy?"
    passage = ("The refund policy permits a refund within thirty days. " * 12)[:600]
    original = QueryVariant(
        variant_id="original",
        kind=QueryVariantKind.ORIGINAL,
        language="en",
        text=question,
    )

    class OversizedPassageRetrieval:
        async def retrieve(self, **kwargs: object) -> ContextRetrievalResult:
            del kwargs
            return ContextRetrievalResult(
                chunks=[
                    ContextChunk(
                        chunk_id=uuid.uuid4(),
                        document_id=uuid.uuid4(),
                        chunk_index=0,
                        content=passage,
                        score=0.8,
                        filename="policy.pdf",
                        chunk_hash=content_hash(passage),
                        semantic_score=0.1,
                        rerank_relevance_score=0.8,
                        evidence_relevance_score=0.8,
                        evidence_score_method="reranker_relevance",
                        evidence_calibration_id=RERANKER_RELEVANCE_CALIBRATION_ID,
                        passage_semantic_score=0.5,
                        passage_char_start=0,
                        passage_char_end=len(passage),
                        passage_score_method="passage_max_cosine",
                        query_variants=(original,),
                        branch_contributions=(
                            BranchContribution(
                                branch_id="original_dense",
                                family="original_dense",
                                query_variant_id=original.variant_id,
                                target_language="en",
                                rank=1,
                                raw_score=0.1,
                                score_type=BranchScoreType.COSINE_SIMILARITY,
                                rrf_score=0.01,
                            ),
                        ),
                        metadata={"rerank_status": "applied"},
                    )
                ],
                diagnostics={"rerank_status": "applied"},
            )

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
            system_prompt_version="v5",
            candidate_wise_grounding_enabled=True,
            context_char_budget=500,
        ),
    )
    service._retrieval = OversizedPassageRetrieval()

    turn = await service.send_message(conversation.id, MessageSendRequest(content=question))

    assert llm.calls == 0
    assert turn.assistant_message.finish_reason == "insufficient_evidence"
    assert turn.assistant_message.insufficient_evidence_reason == "below_relevance_threshold"
    gate = turn.assistant_message.metadata["evidence_gate"]
    assert gate["candidate_wise"]["admitted_count"] == 1
    assert gate["generation_ran"] is False


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
    assert gate["candidate_wise"]["enabled"] is False
    assert gate["candidate_wise"]["path"] == "legacy_shadow"
    assert gate["candidate_wise"]["shadow_sufficient"] is True
    assert gate["candidate_wise"]["assessed_count"] == 1
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
        MessageSendRequest(content="What is the refund policy?"),
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


async def test_indexed_then_web_uses_web_only_after_knowledge_gate_fails(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    conversation.system_prompt_version = "v5"
    web = FakeWebSearch()
    service = _service(
        session,
        conversation_repository,
        message_repository,
        CitedLLM("Current web guidance allows refunds within 30 days [1]."),
        chat_config=ChatConfig(
            response_mode=ResponseMode.INDEXED_THEN_WEB,
            system_prompt_version="v5",
        ),
    )
    service._retrieval = EmptyRetrieval()
    service._web_search = web

    turn = await service.send_message(
        conversation.id,
        MessageSendRequest(content="What is the current refund guidance?"),
    )

    assert len(web.calls) == 1
    assert turn.assistant_message.source_provenance == "web"
    assert turn.assistant_message.metadata["web_search"]["fallback_used"] is True
    assert turn.assistant_message.metadata["web_search"]["status"] == "evidence_accepted"
    assert turn.assistant_message.content.startswith("This wasn\u2019t covered")
    assert [citation.source_kind for citation in turn.assistant_message.citations] == ["web"]
    assert turn.assistant_message.citations[0].web_url == "https://example.test/refunds"
    assert turn.assistant_message.citations[0].chunk_id is None
    assert turn.assistant_message.citations[0].document_id is None
    assert turn.assistant_message.claims[0].evidence[0].chunk_id is None
    assert turn.assistant_message.claims[0].evidence[0].document_id is None
    trace = turn.assistant_message.metadata["retrieval_trace"]["context_selected"][0]
    assert trace["source_kind"] == "web"
    assert trace["web_url"] == "https://example.test/refunds"
    assert "chunk_id" not in trace
    assert "document_id" not in trace


async def test_web_fallback_rejects_uncited_or_irrelevant_evidence(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    conversation.system_prompt_version = "v5"
    web = FakeWebSearch(
        [
            WebSearchEvidence(
                evidence_id="uncited",
                title="Refund policy",
                url="https://example.test/refunds",
                content="Refunds are available within 30 days.",
                retrieved_at=datetime.now(UTC),
                citation_verified=False,
            ),
            WebSearchEvidence(
                evidence_id="irrelevant",
                title="Weather forecast",
                url="https://example.test/weather",
                content="Rain is expected this weekend across the region.",
                retrieved_at=datetime.now(UTC),
                citation_verified=True,
            ),
        ]
    )
    service = _service(
        session,
        conversation_repository,
        message_repository,
        CitedLLM("must not run"),
        chat_config=ChatConfig(
            response_mode=ResponseMode.INDEXED_THEN_WEB,
            system_prompt_version="v5",
        ),
    )
    service._retrieval = EmptyRetrieval()
    service._web_search = web

    turn = await service.send_message(
        conversation.id,
        MessageSendRequest(content="What is the current refund guidance?"),
    )

    acceptance = turn.assistant_message.metadata["web_search"]["acceptance"]
    assert turn.assistant_message.finish_reason == "insufficient_evidence"
    assert acceptance == {
        "accepted_count": 0,
        "rejected_invalid_count": 1,
        "rejected_irrelevant_count": 1,
    }
    assert turn.assistant_message.metadata["web_search"]["status"] == (
        "evidence_extracted_irrelevant"
    )


async def test_web_search_releases_read_transaction_before_network_io(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    conversation.system_prompt_version = "v5"

    class TransactionAwareWebSearch(FakeWebSearch):
        async def search(self, query: str, *, max_results: int) -> WebSearchResult:
            assert session.rollback.await_count >= 1
            return await super().search(query, max_results=max_results)

    service = _service(
        session,
        conversation_repository,
        message_repository,
        CitedLLM("Current web guidance allows refunds within 30 days [1]."),
        chat_config=ChatConfig(
            response_mode=ResponseMode.INDEXED_THEN_WEB,
            system_prompt_version="v5",
        ),
    )
    service._retrieval = EmptyRetrieval()
    service._web_search = TransactionAwareWebSearch()

    turn = await service.send_message(
        conversation.id,
        MessageSendRequest(content="What is the current refund guidance?"),
    )

    assert turn.assistant_message.source_provenance == "web"


async def test_indexed_then_web_skips_web_when_knowledge_is_sufficient(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    conversation.system_prompt_version = "v5"
    web = FakeWebSearch()
    service = _service(
        session,
        conversation_repository,
        message_repository,
        CitedLLM("Refunds are available within 30 days [1]."),
        chat_config=ChatConfig(
            response_mode=ResponseMode.INDEXED_THEN_WEB,
            system_prompt_version="v5",
        ),
    )
    service._web_search = web

    turn = await service.send_message(
        conversation.id,
        MessageSendRequest(content="What is the refund policy?"),
    )

    assert web.calls == []
    assert turn.assistant_message.source_provenance == "knowledge"
    assert turn.assistant_message.metadata["web_search"]["status"] == "not_requested"


async def test_kb_sufficient_turn_survives_missing_web_provider(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    conversation.system_prompt_version = "v5"
    service = _service(
        session,
        conversation_repository,
        message_repository,
        CitedLLM("Refunds are available within 30 days [1]."),
        chat_config=ChatConfig(
            response_mode=ResponseMode.INDEXED_THEN_WEB,
            system_prompt_version="v5",
        ),
    )

    turn = await service.send_message(
        conversation.id,
        MessageSendRequest(content="What is the refund policy?"),
    )

    assert turn.assistant_message.source_provenance == "knowledge"
    assert turn.assistant_message.metadata["web_search"]["status"] == "not_requested"


async def test_missing_web_provider_fails_closed_only_when_web_is_required(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    conversation.system_prompt_version = "v5"
    service = _service(
        session,
        conversation_repository,
        message_repository,
        CitedLLM("must not run"),
        chat_config=ChatConfig(
            response_mode=ResponseMode.INDEXED_THEN_WEB,
            system_prompt_version="v5",
        ),
    )
    service._retrieval = EmptyRetrieval()

    turn = await service.send_message(
        conversation.id,
        MessageSendRequest(content="What is the current refund guidance?"),
    )

    assert turn.assistant_message.finish_reason == "insufficient_evidence"
    assert turn.assistant_message.metadata["web_search"]["status"] == "provider_unavailable"


async def test_observe_mode_does_not_trigger_web_fallback_when_gate_allows_generation(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    conversation.system_prompt_version = "v5"
    web = FakeWebSearch()
    service = _service(
        session,
        conversation_repository,
        message_repository,
        CitedLLM("সঞ্চয়পত্র হইতে অর্জিত মুনাফা [1]."),
        chat_config=ChatConfig(
            response_mode=ResponseMode.INDEXED_THEN_WEB,
            system_prompt_version="v5",
            evidence_gate_mode=EvidenceGateMode.OBSERVE,
        ),
    )
    service._retrieval = NearMissRetrieval()
    service._web_search = web

    turn = await service.send_message(
        conversation.id,
        MessageSendRequest(content="what are the source tax deduction areas?"),
    )

    assert web.calls == []
    assert turn.assistant_message.source_provenance == "knowledge"
    assert turn.assistant_message.metadata["evidence_gate"]["sufficient"] is False
    assert turn.assistant_message.metadata["evidence_gate"]["blocked_generation"] is False


async def test_document_scoped_question_never_escapes_to_web(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    conversation.system_prompt_version = "v5"
    web = FakeWebSearch()
    service = _service(
        session,
        conversation_repository,
        message_repository,
        CitedLLM("unused"),
        chat_config=ChatConfig(
            response_mode=ResponseMode.INDEXED_THEN_WEB,
            system_prompt_version="v5",
        ),
    )
    service._retrieval = EmptyRetrieval()
    service._web_search = web

    turn = await service.send_message(
        conversation.id,
        MessageSendRequest(
            content="What does this document say?",
            document_id=uuid.uuid4(),
        ),
    )

    assert web.calls == []
    assert turn.assistant_message.source_provenance == "none"
    assert turn.assistant_message.metadata["web_search"]["status"] == ("suppressed_scoped_request")
    assert turn.assistant_message.finish_reason == "insufficient_evidence"


@pytest.mark.parametrize(
    ("web", "expected_status"),
    [
        (FakeWebSearch([]), "no_sources"),
        (SourceOnlyWebSearch(), "sources_found_no_extractable_evidence"),
        (FailingWebSearch(), "failed"),
    ],
)
async def test_web_no_result_or_failure_refuses_without_llm_guessing(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
    web: object,
    expected_status: str,
) -> None:
    conversation.system_prompt_version = "v5"

    class CountingLLM(CitedLLM):
        calls = 0

        async def generate(self, messages, *, temperature, max_tokens):
            self.calls += 1
            return await super().generate(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

    llm = CountingLLM("must not run")
    service = _service(
        session,
        conversation_repository,
        message_repository,
        llm,
        chat_config=ChatConfig(
            response_mode=ResponseMode.INDEXED_THEN_WEB,
            system_prompt_version="v5",
        ),
    )
    service._retrieval = EmptyRetrieval()
    service._web_search = web

    turn = await service.send_message(
        conversation.id,
        MessageSendRequest(content="What is the lunar payroll rule?"),
    )

    assert llm.calls == 0
    assert turn.assistant_message.source_provenance == "none"
    assert turn.assistant_message.metadata["web_search"]["status"] == expected_status
    assert turn.assistant_message.finish_reason == "insufficient_evidence"


async def test_indexed_and_web_keeps_provenance_separate(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    conversation.system_prompt_version = "v5"
    conflicting_web = FakeWebSearch(
        [
            WebSearchEvidence(
                evidence_id="web-conflict",
                title="Current refund guidance",
                url="https://example.test/refunds",
                content="Current web guidance limits refunds to 14 days.",
                retrieved_at=datetime.now(UTC),
                citation_verified=True,
            )
        ]
    )
    service = _service(
        session,
        conversation_repository,
        message_repository,
        CitedLLM(
            "The knowledge policy allows refunds within 30 days [1]. "
            "Current web guidance reports 14 days [2]. These sources conflict."
        ),
        chat_config=ChatConfig(
            response_mode=ResponseMode.INDEXED_AND_WEB,
            system_prompt_version="v5",
        ),
    )
    service._web_search = conflicting_web

    turn = await service.send_message(
        conversation.id,
        MessageSendRequest(content="What is the refund policy?"),
    )

    assert turn.assistant_message.source_provenance == "knowledge_and_web"
    assert {citation.source_kind for citation in turn.assistant_message.citations} == {
        "knowledge",
        "web",
    }
    assert "conflict" in turn.assistant_message.content


async def test_bangla_query_uses_web_fallback_with_bangla_notice(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    conversation.system_prompt_version = "v5"
    service = _service(
        session,
        conversation_repository,
        message_repository,
        CitedLLM("Current web guidance allows refunds within 30 days [1]."),
        chat_config=ChatConfig(
            response_mode=ResponseMode.INDEXED_THEN_WEB,
            system_prompt_version="v5",
        ),
    )
    service._retrieval = EmptyRetrieval()
    service._web_search = FakeWebSearch()

    turn = await service.send_message(
        conversation.id,
        MessageSendRequest(content="বর্তমান রিফান্ড নীতি কী?"),
    )

    assert turn.assistant_message.source_provenance == "web"
    assert turn.assistant_message.content.startswith("Knowledge base-এ এটি ছিল না")


async def test_stream_done_event_contains_web_provenance(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    conversation.system_prompt_version = "v5"
    service = _service(
        session,
        conversation_repository,
        message_repository,
        CitedLLM("Current web guidance allows refunds within 30 days [1]."),
        chat_config=ChatConfig(
            response_mode=ResponseMode.INDEXED_THEN_WEB,
            system_prompt_version="v5",
        ),
    )
    service._retrieval = EmptyRetrieval()
    service._web_search = FakeWebSearch()

    events = [
        item
        async for item in service.stream_message(
            conversation.id,
            MessageSendRequest(content="What is the current refund guidance?"),
        )
    ]

    assert isinstance(events[-1], dict)
    assert events[-1]["source_provenance"] == "web"
    assert events[-1]["web_search"]["fallback_used"] is True


async def test_casual_bangla_turn_skips_retrieval_web_and_llm(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    class CountingRetrieval:
        calls = 0

        async def retrieve(self, **kwargs: object) -> ContextRetrievalResult:
            del kwargs
            self.calls += 1
            return ContextRetrievalResult(chunks=[], diagnostics={})

    retrieval = CountingRetrieval()
    web = FakeWebSearch()
    service = _service(
        session,
        conversation_repository,
        message_repository,
        CitedLLM("must not run"),
    )
    service._retrieval = retrieval
    service._web_search = web

    turn = await service.send_message(
        conversation.id,
        MessageSendRequest(content="ধন্যবাদ"),
    )

    assert retrieval.calls == 0
    assert web.calls == []
    assert turn.assistant_message.content == "স্বাগতম।"
    assert turn.assistant_message.source_provenance == "none"
    assert turn.assistant_message.metadata["non_knowledge_turn"] is True
    assert turn.assistant_message.grounded is False


async def test_bangla_insufficient_evidence_uses_friendly_bangla_message(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    service = _service(
        session,
        conversation_repository,
        message_repository,
        CitedLLM("must not run"),
    )
    service._retrieval = EmptyRetrieval()

    turn = await service.send_message(
        conversation.id,
        MessageSendRequest(content="প্রকল্পের ছুটির নীতি কী?"),
    )

    assert turn.assistant_message.finish_reason == "insufficient_evidence"
    assert turn.assistant_message.source_provenance == "none"
    assert "আত্মবিশ্বাসের সঙ্গে" in turn.assistant_message.content


async def test_followup_retrieval_query_is_not_rewritten_by_substring_heuristics(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    captured: list[str] = []

    class CapturingRetrieval(FakeRetrieval):
        async def retrieve(self, **kwargs: object) -> ContextRetrievalResult:
            captured.append(str(kwargs["query"]))
            return await super().retrieve(**kwargs)

    service = _service(
        session,
        conversation_repository,
        message_repository,
        CitedLLM("Refunds are available within 30 days [1]."),
    )
    service._retrieval = CapturingRetrieval()

    await service.send_message(
        conversation.id,
        MessageSendRequest(content="What is it used for?"),
    )

    assert captured == ["What is it used for?"]
