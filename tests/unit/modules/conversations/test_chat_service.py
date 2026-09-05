"""Unit tests for ChatService."""

from __future__ import annotations

import asyncio
import json
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
from app.modules.conversations.prompts.registry import GROUNDED_PROMPT_VERSION
from app.modules.conversations.schemas.message import MessageSendRequest
from app.modules.conversations.services.chat_service import (
    ChatService,
    _scope_current_authority_status,
)
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
    """Same-language English evidence whose whole-chunk cosine sits just under 0.35."""

    chunk_id = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")

    async def retrieve(self, **kwargs: object) -> ContextRetrievalResult:
        del kwargs
        return ContextRetrievalResult(
            chunks=[
                ContextChunk(
                    chunk_id=self.chunk_id,
                    document_id=uuid.uuid4(),
                    chunk_index=0,
                    content=(
                        "Office stationery rules occupy most of this chapter. "
                        "Parking permits are issued on Tuesdays only."
                    ),
                    score=0.018,
                    filename="policy.pdf",
                    chunk_hash="office-stationery",
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
    store_candidate_trace: bool = True,
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
        store_candidate_trace=store_candidate_trace,
    )


async def test_runtime_metadata_keeps_funnel_but_omits_candidate_payloads(
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
        store_candidate_trace=False,
    )
    service._retrieval = NearMissRetrieval()

    turn = await service.send_message(
        conversation.id,
        MessageSendRequest(content="What are the source tax deduction categories?"),
    )

    metadata = turn.assistant_message.metadata
    assert metadata["retrieval_trace"]["candidates"] == []
    assert metadata["retrieval_trace"]["context_selected"] == []
    assert "assessments" not in metadata["evidence_gate"]["candidate_wise"]
    assert metadata["evidence_funnel"]["assessed"] == 1


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
        ),
    )
    service._retrieval = CandidateRetrieval()

    turn = await service.send_message(conversation.id, MessageSendRequest(content=question))

    assert llm.calls == 1
    assert relevant_text in captured_system[0]
    assert unrelated_text not in captured_system[0]
    assert len(turn.assistant_message.citations) == 1
    assert turn.assistant_message.citations[0].chunk_id == relevant.chunk_id
    assert turn.assistant_message.prompt_version == GROUNDED_PROMPT_VERSION
    candidate_diagnostics = turn.assistant_message.metadata["evidence_gate"]["candidate_wise"]
    assert candidate_diagnostics["assessed_count"] == 2
    assert candidate_diagnostics["admitted_count"] == 1
    assert candidate_diagnostics["retrieved_count"] == 2
    assert candidate_diagnostics["reranked_count"] == 2
    assert candidate_diagnostics["removed_count"] == 0
    assert candidate_diagnostics["context_selected_count"] == 1
    assert candidate_diagnostics["cited_count"] == 1
    assert candidate_diagnostics["alerts"] == {
        "unknown_calibration_count": 0,
        "failed_span_derivation_count": 0,
        "missing_provenance_count": 0,
        "span_hash_mismatch_count": 0,
    }
    funnel = turn.assistant_message.metadata["evidence_funnel"]
    assert funnel["assessed"] == 2
    assert funnel["admitted"] == 1
    assert funnel["context_selected"] == 1
    assert funnel["cited"] == 1
    assert funnel["outcome"] == "answered"


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
            context_char_budget=500,
        ),
    )
    service._retrieval = OversizedPassageRetrieval()

    turn = await service.send_message(conversation.id, MessageSendRequest(content=question))

    assert llm.calls == 0
    assert turn.assistant_message.finish_reason == "insufficient_evidence"
    assert turn.assistant_message.insufficient_evidence_reason == "context_selection_empty"
    gate = turn.assistant_message.metadata["evidence_gate"]
    assert gate["failure_stage"] == "context_selection"
    assert gate["candidate_wise"]["admitted_count"] == 1
    assert gate["candidate_wise"]["context_selected_count"] == 0
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
    assert assistant.message_metadata["evidence_funnel"]["outcome"] == "failed"


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
    assert gate["context_selection"]["observe_context"] == "ranked_candidates"
    assert turn.assistant_message.metadata["evidence_funnel"]["would_have_blocked"] is True
    assert turn.assistant_message.metadata["evidence_funnel"]["observe_context"] == (
        "ranked_candidates"
    )


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
                        rank_score=0.8693157,
                        rerank_relevance_score=0.8693157,
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
    assert gate["candidate_wise"]["path"] == "candidate_wise"
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
    assert not turn.assistant_message.content.startswith("This wasn\u2019t covered")
    assert any(item.kind == "web_evidence_used" for item in turn.assistant_message.notices)
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


async def test_modifies_expansion_survives_combined_rerank_and_skips_web(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    conversation.system_prompt_version = "v5"
    question = "What is the current refund amendment?"
    base_text = "The original leave policy required manager approval."
    modifier_text = "The current refund amendment permits a refund within thirty days."
    base_document_id = uuid.uuid4()
    modifier_document_id = uuid.uuid4()
    base_revision_id = uuid.uuid4()
    modifier_revision_id = uuid.uuid4()
    original = QueryVariant(
        variant_id="original",
        kind=QueryVariantKind.ORIGINAL,
        language="en",
        text=question,
    )
    recall_provenance = {
        "relationship_type": "modifies",
        "depth": 1,
        "base_revision_id": str(base_revision_id),
        "base_document_id": str(base_document_id),
        "modifier_revision_id": str(modifier_revision_id),
        "modifier_document_id": str(modifier_document_id),
    }

    def candidate(
        *,
        content: str,
        document_id: uuid.UUID,
        score: float,
        related: bool,
    ) -> ContextChunk:
        return ContextChunk(
            chunk_id=uuid.uuid4(),
            document_id=document_id,
            chunk_index=0,
            content=content,
            score=score,
            filename="amendment.pdf" if related else "policy.pdf",
            chunk_hash=content_hash(content),
            semantic_score=0.1,
            rerank_relevance_score=score,
            evidence_relevance_score=score,
            evidence_score_method="reranker_relevance",
            evidence_calibration_id=RERANKER_RELEVANCE_CALIBRATION_ID,
            query_variants=(original,),
            branch_contributions=(
                BranchContribution(
                    branch_id="original_lexical",
                    family="original_lexical",
                    query_variant_id=original.variant_id,
                    target_language="en",
                    rank=1 if related else 8,
                    raw_score=8.0 if related else 0.1,
                    score_type=BranchScoreType.KEYWORD_BM25,
                    rrf_score=0.016 if related else 0.002,
                ),
            ),
            metadata={
                "rerank_status": "applied",
                "retrieval_scope": "related_modifier" if related else "direct",
                "relationship_grounding_trust": False,
                "relationship_recall_provenance": [recall_provenance] if related else [],
            },
        )

    base = candidate(
        content=base_text,
        document_id=base_document_id,
        score=0.92,
        related=False,
    )
    modifier = candidate(
        content=modifier_text,
        document_id=modifier_document_id,
        score=0.81,
        related=True,
    )
    expansion_record = {
        **recall_provenance,
        "outcome": "expanded",
        "candidate_count": 1,
        "retained_candidate_count": 1,
        "modifier_effective_from": "2026-07-01T00:00:00+00:00",
    }

    class AuthorityRetrieval:
        calls: list[dict[str, object]]

        def __init__(self) -> None:
            self.calls = []

        async def retrieve(self, **kwargs: object) -> ContextRetrievalResult:
            self.calls.append(kwargs)
            if kwargs.get("document_id") is not None:
                return ContextRetrievalResult(
                    chunks=[base],
                    diagnostics={
                        "rerank_status": "applied",
                        "modifies_expansion_status": "suppressed_document_scope",
                        "modifies_expansion_depth": 1,
                        "modifies_expansion_records": [expansion_record],
                        "related_source_count": 0,
                        "relationship_candidate_count": 0,
                        "retrieved_candidate_count": 1,
                        "reranked_candidate_count": 1,
                        "post_rerank_removed_count": 0,
                    },
                )
            return ContextRetrievalResult(
                chunks=[base, modifier],
                diagnostics={
                    "rerank_status": "applied",
                    "modifies_expansion_status": "expanded",
                    "modifies_expansion_depth": 1,
                    "modifies_expansion_records": [expansion_record],
                    "related_source_count": 1,
                    "relationship_candidate_count": 1,
                    "retrieved_candidate_count": 5,
                    "reranked_candidate_count": 4,
                    "post_rerank_removed_count": 2,
                    "post_rerank_removal_reasons": {
                        "source_policy": 1,
                        "duplicate_content": 1,
                    },
                },
            )

    captured_system: list[str] = []

    class CapturingLLM(EchoLLMProvider):
        calls = 0

        async def generate(self, messages, *, temperature, max_tokens):
            del temperature, max_tokens
            self.calls += 1
            captured_system.append(messages[0].content)
            return ChatCompletionResult(
                content=f"{modifier_text} [1]",
                provider="echo",
                model="test",
                finish_reason="stop",
                usage=ChatUsage(10, 5),
                provider_version="1",
            )

    llm = CapturingLLM(model="test", provider_version="1")
    web = FakeWebSearch()
    retrieval = AuthorityRetrieval()
    service = _service(
        session,
        conversation_repository,
        message_repository,
        llm,
        chat_config=ChatConfig(
            system_prompt_version="v5",
            response_mode=ResponseMode.INDEXED_THEN_WEB,
        ),
    )
    service._retrieval = retrieval
    service._web_search = web

    turn = await service.send_message(conversation.id, MessageSendRequest(content=question))

    assert retrieval.calls[0].get("document_id") is None
    assert llm.calls == 1
    assert web.calls == []
    assert modifier_text in captured_system[0]
    assert base_text not in captured_system[0]
    assert turn.assistant_message.source_provenance == "knowledge"
    assert turn.assistant_message.metadata["web_search"]["status"] == "not_requested"
    assert turn.assistant_message.metadata["current_authority"]["status"] == "expanded"
    assert turn.assistant_message.metadata["current_authority"]["related_source_count"] == 1
    assert turn.assistant_message.metadata["current_authority"]["post_rerank_removed_count"] == 2
    citation = turn.assistant_message.citations[0]
    assert citation.document_id == modifier_document_id
    assert citation.chunk_id == modifier.chunk_id
    assert citation.relationship_recall_provenance[0]["modifier_document_id"] == str(
        modifier_document_id
    )
    candidate_diagnostics = turn.assistant_message.metadata["evidence_gate"]["candidate_wise"]
    assert candidate_diagnostics["assessed_count"] == 2
    assert candidate_diagnostics["admitted_count"] == 1
    assert candidate_diagnostics["retrieved_count"] == 5
    assert candidate_diagnostics["reranked_count"] == 4
    assert candidate_diagnostics["removed_count"] == 2
    assert candidate_diagnostics["cited_count"] == 1

    scoped = await service.send_message(
        conversation.id,
        MessageSendRequest(content=question, document_id=base_document_id),
    )

    assert retrieval.calls[1]["document_id"] == base_document_id
    assert web.calls == []
    assert llm.calls == 1
    assert scoped.assistant_message.metadata["current_authority"]["status"] == (
        "suppressed_document_scope"
    )
    assert scoped.assistant_message.metadata["scope_current_authority"]["status"] == (
        "effective_modifier_excluded_by_scope"
    )
    notices = scoped.assistant_message.notices
    assert any(item.kind == "scope_excludes_effective_modifier" for item in notices)
    assert scoped.assistant_message.metadata["web_search"]["status"] == "not_requested"
    scoped_gate = scoped.assistant_message.metadata["evidence_gate"]["candidate_wise"]
    assert scoped_gate["assessed_count"] == 1
    assert scoped_gate["admitted_count"] == 0


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
    web_notices = [
        item for item in turn.assistant_message.notices if item.kind == "web_evidence_used"
    ]
    assert web_notices and web_notices[0].language == "bn"


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
    assert any(item["kind"] == "web_evidence_used" for item in events[-1]["notices"])


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


def test_scope_notice_ignores_modifiers_outside_as_of() -> None:
    request = MessageSendRequest(content="Current rate?", document_id=uuid.uuid4())
    status = _scope_current_authority_status(
        request,
        {
            "modifies_expansion_status": "suppressed_document_scope",
            "modifies_expansion_records": [
                {
                    "relationship_type": "modifies",
                    "modifier_effective_from": "2027-07-01",
                    "outcome": "outside_as_of",
                }
            ],
        },
    )
    assert status is None


def test_scope_notice_keeps_expanded_effective_modifiers() -> None:
    request = MessageSendRequest(content="Current rate?", document_id=uuid.uuid4())
    status = _scope_current_authority_status(
        request,
        {
            "modifies_expansion_status": "suppressed_document_scope",
            "modifies_expansion_records": [
                {
                    "relationship_type": "modifies",
                    "modifier_effective_from": "2026-07-01",
                    "outcome": "expanded",
                }
            ],
        },
    )
    assert status is not None
    assert status["status"] == "effective_modifier_excluded_by_scope"
    assert status["excluded_effective_modifier_count"] == 1


class CapturingRetrieval(FakeRetrieval):
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def retrieve(self, **kwargs: object) -> ContextRetrievalResult:
        self.calls.append(kwargs)
        return await FakeRetrieval.retrieve(self, **kwargs)


class ScriptedResolutionLLM(EchoLLMProvider):
    def __init__(
        self,
        resolution: dict[str, object],
        *,
        answer: str = "grounded answer [1]",
    ) -> None:
        super().__init__(model="test", provider_version="1")
        self.resolution = resolution
        self.answer = answer
        self.generate_calls = 0
        self.resolver_calls = 0
        self.generation_prompts: list[list] = []

    @staticmethod
    def _is_resolver(messages: list) -> bool:
        return any(
            "Return one JSON object and nothing else" in message.content for message in messages
        )

    async def generate(self, messages, *, temperature, max_tokens):
        self.generate_calls += 1
        if self._is_resolver(messages):
            self.resolver_calls += 1
            assert temperature is None
            return ChatCompletionResult(
                content=json.dumps(self.resolution),
                provider="echo",
                model="test",
                finish_reason="stop",
                usage=ChatUsage(3, 5),
                provider_version="1",
            )
        self.generation_prompts.append(list(messages))
        return ChatCompletionResult(
            content=self.answer,
            provider="echo",
            model="test",
            finish_reason="stop",
            usage=ChatUsage(7, 9),
            provider_version="1",
        )


def _history_messages(
    conversation: Conversation,
    *,
    user_content: str,
    assistant_content: str,
    assistant_finish_reason: str | None = "stop",
) -> tuple[Message, Message]:
    user = Message(
        id=uuid.uuid4(),
        project_id=conversation.project_id,
        conversation_id=conversation.id,
        role=MessageRole.USER,
        content=user_content,
        created_at=datetime(2026, 7, 1, tzinfo=UTC),
        updated_at=datetime(2026, 7, 1, tzinfo=UTC),
    )
    assistant = Message(
        id=uuid.uuid4(),
        project_id=conversation.project_id,
        conversation_id=conversation.id,
        role=MessageRole.ASSISTANT,
        content=assistant_content,
        finish_reason=assistant_finish_reason,
        created_at=datetime(2026, 7, 1, 0, 0, 1, tzinfo=UTC),
        updated_at=datetime(2026, 7, 1, 0, 0, 1, tzinfo=UTC),
        citations=[],
    )
    return user, assistant


def _resolved_payload(
    *,
    relation: str,
    effective_question: str,
    bindings: list[dict[str, object]] | None = None,
    outcome: str = "resolved",
    clarification_question: str | None = None,
    reason: str | None = None,
) -> dict[str, object]:
    return {
        "outcome": outcome,
        "relation": relation,
        "effective_question": effective_question,
        "active_bindings": bindings or [],
        "temporal_intent": {
            "kind": "none",
            "anchor_date": None,
            "requires_snapshot": False,
            "snapshot_origin": None,
        },
        "clarification_question": clarification_question,
        "reason": reason,
    }


async def test_follow_up_retrieves_effective_question_and_keeps_original_prompt(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    prior_user, prior_assistant = _history_messages(
        conversation,
        user_content="What rebate applies to 75,000?",
        assistant_content="The rebate is 11,250.",
    )
    message_repository.list_recent_for_conversation.return_value = [prior_user, prior_assistant]
    retrieval = CapturingRetrieval()
    llm = ScriptedResolutionLLM(
        _resolved_payload(
            relation="follow_up",
            effective_question="What rebate applies to 75,000?",
        )
    )
    service = _service(session, conversation_repository, message_repository, llm)
    service._retrieval = retrieval

    turn = await service.send_message(
        conversation.id,
        MessageSendRequest(content="Explain that more simply."),
    )

    assert retrieval.calls[0]["query"] == "What rebate applies to 75,000?"
    assert llm.resolver_calls == 1
    assert turn.assistant_message.metadata["turn_resolution"]["outcome"] == "resolved"
    assert turn.assistant_message.metadata["turn_resolution"]["query_changed"] is True
    assert turn.assistant_message.metadata["turn_resolution"]["filter_changed"] is False
    prompt = llm.generation_prompts[0]
    assert prompt[-1].content == "Explain that more simply."
    assert any("Validated conversation interpretation" in message.content for message in prompt)


async def test_adopted_value_is_scenario_input_and_filters_stay_authoritative(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    prior_user, prior_assistant = _history_messages(
        conversation,
        user_content="What rebate applies to 75,000?",
        assistant_content="The calculated rebate is 7,500.",
    )
    message_repository.list_recent_for_conversation.return_value = [prior_user, prior_assistant]
    retrieval = CapturingRetrieval()
    scoped_document = uuid.uuid4()
    llm = ScriptedResolutionLLM(
        _resolved_payload(
            relation="follow_up",
            effective_question="What fee applies if my monthly budget is 7,500?",
            bindings=[
                {
                    "kind": "scenario_parameter",
                    "active_value": "7,500",
                    "origin": "user_adopted_assistant",
                    "references": [
                        {
                            "message_id": str(prior_assistant.id),
                            "role": "assistant",
                            "field": "content",
                            "excerpt": "7,500",
                        },
                        {
                            "message_id": "CURRENT",
                            "role": "user",
                            "field": "content",
                            "excerpt": "Use that amount",
                        },
                    ],
                }
            ],
        )
    )
    service = _service(session, conversation_repository, message_repository, llm)
    service._retrieval = retrieval
    current = "Use that amount as my next monthly budget. What fee applies?"

    async def send() -> object:
        # Patch the current-message id into the script after the user row exists.
        return await service.send_message(
            conversation.id,
            MessageSendRequest(content=current, document_id=scoped_document),
        )

    # Bindings reference the current user message id assigned during persist.
    original_generate = llm.generate

    async def generate_with_current_id(messages, *, temperature, max_tokens):
        if llm._is_resolver(messages) and llm.resolution["active_bindings"]:
            binding = llm.resolution["active_bindings"][0]
            for reference in binding["references"]:
                if reference["message_id"] == "CURRENT":
                    reference["message_id"] = str(
                        message_repository.add.call_args_list[0].args[0].id
                    )
        return await original_generate(messages, temperature=temperature, max_tokens=max_tokens)

    llm.generate = generate_with_current_id  # type: ignore[method-assign]
    turn = await send()

    assert retrieval.calls[0]["query"] == "What fee applies if my monthly budget is 7,500?"
    assert retrieval.calls[0]["document_id"] == scoped_document
    recorded = turn.assistant_message.metadata["turn_resolution"]
    assert recorded["active_bindings"][0]["origin"] == "user_adopted_assistant"
    assert recorded["active_bindings"][0]["active_value"] == "7,500"
    assert recorded["filter_changed"] is False


async def test_correction_replaces_active_amount_in_retrieval_query(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    prior_user, prior_assistant = _history_messages(
        conversation,
        user_content="Rebate on 75,000?",
        assistant_content="The rebate is 11,250.",
    )
    message_repository.list_recent_for_conversation.return_value = [prior_user, prior_assistant]
    retrieval = CapturingRetrieval()
    llm = ScriptedResolutionLLM(
        _resolved_payload(
            relation="correction",
            effective_question="What rebate applies to 90,000?",
            bindings=[
                {
                    "kind": "scenario_parameter",
                    "active_value": "90,000",
                    "origin": "user_literal",
                    "references": [
                        {
                            "message_id": "CURRENT",
                            "role": "user",
                            "field": "content",
                            "excerpt": "90,000",
                        }
                    ],
                }
            ],
        )
    )
    original_generate = llm.generate

    async def generate_with_current_id(messages, *, temperature, max_tokens):
        if llm._is_resolver(messages):
            for binding in llm.resolution["active_bindings"]:
                for reference in binding["references"]:
                    if reference["message_id"] == "CURRENT":
                        reference["message_id"] = str(
                            message_repository.add.call_args_list[0].args[0].id
                        )
        return await original_generate(messages, temperature=temperature, max_tokens=max_tokens)

    llm.generate = generate_with_current_id  # type: ignore[method-assign]
    service = _service(session, conversation_repository, message_repository, llm)
    service._retrieval = retrieval

    await service.send_message(
        conversation.id,
        MessageSendRequest(content="90,000, not 75,000. What rebate applies?"),
    )

    assert retrieval.calls[0]["query"] == "What rebate applies to 90,000?"
    assert "75,000" not in retrieval.calls[0]["query"]


async def test_clarification_skips_retrieval_and_keeps_grounded_null(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    prior_user, prior_assistant = _history_messages(
        conversation,
        user_content="Compare standard and premium support.",
        assistant_content="Standard answers in 8 hours. Premium answers in 1 hour.",
    )
    message_repository.list_recent_for_conversation.return_value = [prior_user, prior_assistant]
    retrieval = CapturingRetrieval()
    llm = ScriptedResolutionLLM(
        _resolved_payload(
            outcome="clarify",
            relation="follow_up",
            effective_question="Which plan?",
            clarification_question="Do you mean the standard plan or the premium plan?",
            reason="ambiguous_referent",
        )
    )
    service = _service(session, conversation_repository, message_repository, llm)
    service._retrieval = retrieval

    turn = await service.send_message(
        conversation.id,
        MessageSendRequest(content="What is the response time?"),
    )

    assert retrieval.calls == []
    assert llm.resolver_calls == 1
    assert llm.generate_calls == 1
    assert turn.assistant_message.finish_reason == "clarification"
    assert turn.assistant_message.grounded is None
    assert turn.assistant_message.claims == []
    assert turn.assistant_message.citations == []
    assert turn.assistant_message.insufficient_evidence_reason is None
    assert turn.assistant_message.source_provenance == "none"
    gate = turn.assistant_message.metadata["evidence_gate"]
    assert gate["claims_status"] == "not_applicable"
    assert gate["generation_ran"] is False
    assert turn.assistant_message.metadata["evidence_funnel"]["outcome"] == "clarification"
    assert "Do you mean the standard plan" in turn.assistant_message.content


async def test_clarification_streams_without_evidence_claims(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    prior_user, prior_assistant = _history_messages(
        conversation,
        user_content="Tell me about the two plans.",
        assistant_content="There is a standard plan and a premium plan.",
    )
    message_repository.list_recent_for_conversation.return_value = [prior_user, prior_assistant]
    llm = ScriptedResolutionLLM(
        _resolved_payload(
            outcome="clarify",
            relation="follow_up",
            effective_question="Which plan?",
            clarification_question="Which plan should I use?",
            reason="ambiguous_referent",
        )
    )
    service = _service(session, conversation_repository, message_repository, llm)
    events: list[object] = []
    async for event in service.stream_message(
        conversation.id,
        MessageSendRequest(content="How fast is it?"),
    ):
        events.append(event)
    assert events[0] == "Which plan should I use?"
    done = events[-1]
    assert isinstance(done, dict)
    assert done["finish_reason"] == "clarification"
    assert done["grounded"] is None
    assert done["claims"] == []
    assert done["citations"] == []
    assert done["turn_resolution"]["outcome"] == "clarify"


async def test_short_clarification_reply_resolves_and_retrieves(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    first_user, first_assistant = _history_messages(
        conversation,
        user_content="Compare standard and premium support.",
        assistant_content="Standard is 8 hours. Premium is 1 hour.",
    )
    clarify_user = Message(
        id=uuid.uuid4(),
        project_id=conversation.project_id,
        conversation_id=conversation.id,
        role=MessageRole.USER,
        content="What is the response time?",
        created_at=datetime(2026, 7, 1, 0, 0, 2, tzinfo=UTC),
        updated_at=datetime(2026, 7, 1, 0, 0, 2, tzinfo=UTC),
    )
    clarify_assistant = Message(
        id=uuid.uuid4(),
        project_id=conversation.project_id,
        conversation_id=conversation.id,
        role=MessageRole.ASSISTANT,
        content="Do you mean the standard plan or the premium plan?",
        finish_reason="clarification",
        created_at=datetime(2026, 7, 1, 0, 0, 3, tzinfo=UTC),
        updated_at=datetime(2026, 7, 1, 0, 0, 3, tzinfo=UTC),
        citations=[],
    )
    message_repository.list_recent_for_conversation.return_value = [
        first_user,
        first_assistant,
        clarify_user,
        clarify_assistant,
    ]
    retrieval = CapturingRetrieval()
    llm = ScriptedResolutionLLM(
        _resolved_payload(
            relation="follow_up",
            effective_question="What is the premium support response time?",
        )
    )
    service = _service(session, conversation_repository, message_repository, llm)
    service._retrieval = retrieval

    turn = await service.send_message(
        conversation.id,
        MessageSendRequest(content="premium"),
    )

    assert retrieval.calls[0]["query"] == "What is the premium support response time?"
    assert turn.assistant_message.metadata["turn_resolution"]["outcome"] == "resolved"


async def test_invalid_resolver_output_falls_back_to_raw_message(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    prior_user, prior_assistant = _history_messages(
        conversation,
        user_content="What is the rebate?",
        assistant_content="15 percent.",
    )
    message_repository.list_recent_for_conversation.return_value = [prior_user, prior_assistant]
    retrieval = CapturingRetrieval()
    service = _service(
        session,
        conversation_repository,
        message_repository,
        EchoLLMProvider(model="test", provider_version="1"),
    )
    service._retrieval = retrieval

    turn = await service.send_message(
        conversation.id,
        MessageSendRequest(content="What is the current rebate rate?"),
    )

    assert retrieval.calls[0]["query"] == "What is the current rebate rate?"
    recorded = turn.assistant_message.metadata["turn_resolution"]
    assert recorded["outcome"] == "fallback"
    assert recorded["failure_code"] == "malformed_output"


async def test_resolver_timeout_falls_back_without_using_interpretation(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prior_user, prior_assistant = _history_messages(
        conversation,
        user_content="What is the rebate?",
        assistant_content="15 percent.",
    )
    message_repository.list_recent_for_conversation.return_value = [prior_user, prior_assistant]
    monkeypatch.setattr(
        "app.modules.conversations.services.chat_service.RESOLUTION_TIMEOUT_SECONDS",
        0.05,
    )

    class SlowThenEcho(EchoLLMProvider):
        resolver_calls = 0

        async def generate(self, messages, *, temperature, max_tokens):
            if any("Return one JSON object" in message.content for message in messages):
                self.resolver_calls += 1
                await asyncio.sleep(1)
            return await super().generate(messages, temperature=temperature, max_tokens=max_tokens)

    retrieval = CapturingRetrieval()
    llm = SlowThenEcho(model="test", provider_version="1")
    service = _service(session, conversation_repository, message_repository, llm)
    service._retrieval = retrieval

    turn = await service.send_message(
        conversation.id,
        MessageSendRequest(content="What is the current rebate rate?"),
    )

    assert llm.resolver_calls == 1
    assert retrieval.calls[0]["query"] == "What is the current rebate rate?"
    assert turn.assistant_message.metadata["turn_resolution"]["failure_code"] == "timeout"


async def test_resolver_cancellation_does_not_become_fallback(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    prior_user, prior_assistant = _history_messages(
        conversation,
        user_content="What is the rebate?",
        assistant_content="15 percent.",
    )
    message_repository.list_recent_for_conversation.return_value = [prior_user, prior_assistant]
    retrieval = CapturingRetrieval()

    class CancellingLLM(EchoLLMProvider):
        async def generate(self, messages, *, temperature, max_tokens):
            if any("Return one JSON object" in message.content for message in messages):
                raise asyncio.CancelledError
            return await super().generate(messages, temperature=temperature, max_tokens=max_tokens)

    service = _service(
        session,
        conversation_repository,
        message_repository,
        CancellingLLM(model="test", provider_version="1"),
    )
    service._retrieval = retrieval

    with pytest.raises(asyncio.CancelledError):
        await service.send_message(
            conversation.id,
            MessageSendRequest(content="What is the current rebate rate?"),
        )
    assert retrieval.calls == []


async def test_resolver_runs_only_after_read_transaction_release(
    session: AsyncMock,
    conversation_repository: AsyncMock,
    message_repository: AsyncMock,
    conversation: Conversation,
) -> None:
    prior_user, prior_assistant = _history_messages(
        conversation,
        user_content="What is the rebate?",
        assistant_content="15 percent.",
    )
    message_repository.list_recent_for_conversation.return_value = [prior_user, prior_assistant]
    retrieval = CapturingRetrieval()
    llm = ScriptedResolutionLLM(
        _resolved_payload(
            relation="follow_up",
            effective_question="What is the current rebate rate?",
        )
    )
    original_generate = llm.generate

    async def generate_and_assert(messages, *, temperature, max_tokens):
        if llm._is_resolver(messages):
            assert session.rollback.await_count >= 1
            assert retrieval.calls == []
        return await original_generate(messages, temperature=temperature, max_tokens=max_tokens)

    llm.generate = generate_and_assert  # type: ignore[method-assign]
    service = _service(session, conversation_repository, message_repository, llm)
    service._retrieval = retrieval

    await service.send_message(
        conversation.id,
        MessageSendRequest(content="What is the current rebate rate?"),
    )
    assert retrieval.calls


@pytest.mark.parametrize("streamed", [False, True])
@pytest.mark.parametrize("conflict", [False, True])
async def test_snapshot_scope_and_clarification_have_streaming_parity(
    session,
    conversation_repository,
    message_repository,
    conversation,
    streamed,
    conflict,
):
    prior_user, prior_assistant = _history_messages(
        conversation,
        user_content="What is the rate?",
        assistant_content="The rate is 15%.",
    )
    message_repository.list_recent_for_conversation.return_value = [prior_user, prior_assistant]
    llm = ScriptedResolutionLLM(
        _resolved_payload(
            relation="follow_up",
            effective_question="What was the rate on 2025-06-01?",
        )
    )
    llm.resolution["temporal_intent"] = {
        "kind": "exact_date",
        "anchor_date": "2025-06-01",
        "requires_snapshot": True,
    }
    original_generate = llm.generate

    async def generate(messages, *, temperature, max_tokens):
        if llm._is_resolver(messages):
            payload = json.loads(messages[-1].content)
            llm.resolution["active_bindings"] = [
                {
                    "kind": "period_date",
                    "active_value": "2025-06-01",
                    "origin": "user_literal",
                    "references": [
                        {
                            "message_id": payload["current_message_id"],
                            "role": "user",
                            "excerpt": "2025-06-01",
                        }
                    ],
                }
            ]
        return await original_generate(messages, temperature=temperature, max_tokens=max_tokens)

    llm.generate = generate
    service = _service(session, conversation_repository, message_repository, llm)
    retrieval = CapturingRetrieval()
    service._retrieval = retrieval
    service._chat_config = service._chat_config.model_copy(
        update={
            "response_mode": ResponseMode.INDEXED_AND_WEB,
        }
    )
    document = uuid.uuid4()
    request = MessageSendRequest(
        content="Check on 2025-06-01.",
        document_id=document,
        metadata_filter={"team": "sales"},
        as_of=datetime(2026, 6, 1, tzinfo=UTC) if conflict else None,
    )
    if streamed:
        events = [event async for event in service.stream_message(conversation.id, request)]
        done = events[-1]
        assert done["turn_resolution"]["outcome"] == ("clarify" if conflict else "resolved")
        if conflict:
            assert done["finish_reason"] == "clarification"
            assert done["grounded"] is None
            assert done["claims"] == done["citations"] == []
    else:
        result = await service.send_message(conversation.id, request)
        if conflict:
            assert result.assistant_message.finish_reason == "clarification"
            assert result.assistant_message.grounded is None
    if conflict:
        assert retrieval.calls == []
        assert llm.generate_calls == 1
    else:
        assert len(retrieval.calls) == 1
        assert retrieval.calls[0]["as_of"] == datetime(2025, 6, 1, tzinfo=UTC)
        assert retrieval.calls[0]["document_id"] == document
        assert retrieval.calls[0]["metadata_filter"] == {"team": "sales"}
    assert request.as_of == (datetime(2026, 6, 1, tzinfo=UTC) if conflict else None)


@pytest.mark.parametrize(
    ("resolver", "generation", "expected"),
    [
        (None, (7, 9), (7, 9)),
        (ChatUsage(3, 5), (7, 9), (10, 14)),
        (ChatUsage(None, None), (7, 9), (None, None)),
        (ChatUsage(3, None), (7, 9), (10, None)),
        (ChatUsage(3, 5), (None, None), (None, None)),
    ],
)
def test_turn_token_totals_distinguish_bypass_from_unknown_usage(resolver, generation, expected):
    from app.modules.conversations.services.chat_service import _combine_token_counts

    assert _combine_token_counts(resolver, *generation) == expected
