"""Unit tests for ChatService."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import replace
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
from app.modules.conversations.citation_snapshots import EVIDENCE_PROVENANCE_VERSION
from app.modules.conversations.context_builder import ContextBuilder
from app.modules.conversations.grounded_context import select_exact_recalled_knowledge
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
from app.platform.providers.errors import ProviderError, ProviderQuotaError, ProviderTimeoutError
from app.platform.providers.implementations.echo_chat import EchoLLMProvider
from app.platform.providers.request_work import current_request_work

pytestmark = pytest.mark.unit


def test_exact_recalled_selection_keeps_authority_redaction() -> None:
    base = ContextChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=0,
        content="Section 1\nThe earlier rule required filing within 30 days.",
        score=0.9,
        filename="rules.md",
        chunk_hash="base",
        metadata={"source_revision_id": "base-revision"},
    )
    current = replace(
        base,
        chunk_id=uuid.uuid4(),
        chunk_index=1,
        content="Section 1\nThe current rule requires filing within 21 days.",
        chunk_hash="current",
        metadata={"source_revision_id": "modifier-revision"},
    )
    selected = select_exact_recalled_knowledge(
        context_builder=ContextBuilder(
            ChatConfig(),
            evidence_approach="authoritative",
            question="Make that shorter.",
        ),
        chunks=[base, current],
        expansion_records=[
            {
                "base_revision_id": "base-revision",
                "modifier_revision_id": "modifier-revision",
                "relationship_type": "modifies",
                "outcome": "expanded",
                "target_provisions": ["Section 1"],
            }
        ],
    )

    assert [chunk.chunk_id for chunk in selected] == [current.chunk_id]


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


class UnresolvedRuleRetrieval(FakeRetrieval):
    """Strong relevance plus a real-corpus-shaped, incomplete MODIFIES edge."""

    async def retrieve(self, **kwargs: object) -> ContextRetrievalResult:
        result = await super().retrieve(**kwargs)
        revision = str(uuid.uuid4())
        return ContextRetrievalResult(
            chunks=[replace(result.chunks[0], metadata={"source_revision_id": revision})],
            diagnostics={
                **result.diagnostics,
                "modifies_expansion_records": [
                    {
                        "base_revision_id": revision,
                        "modifier_revision_id": str(uuid.uuid4()),
                        "relationship_type": "modifies",
                        "outcome": "ungoverned_or_incomplete_metadata",
                        "modifier_effective_from": None,
                        "target_provisions": [],
                    }
                ],
            },
        )


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
        content = self.content
        if (
            messages
            and "Review web source relevance before answer generation" in messages[0].content
        ):
            sources = json.loads(messages[1].content)["sources"]
            content = json.dumps(
                {
                    "accepted": [
                        {"source_index": item["source_index"], "quote": item["content"]}
                        for item in sources
                    ]
                }
            )
        del messages, temperature, max_tokens
        return ChatCompletionResult(
            content=content,
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


async def test_cited_counts_and_authority_use_answer_markers_without_renumbering_snapshots(
    session,
    conversation_repository,
    message_repository,
    conversation,
):
    first = (await FakeRetrieval().retrieve()).chunks[0]
    first = replace(first, metadata={"source_revision_id": str(uuid.uuid4())})
    second = replace(
        first,
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        score=0.8,
        content="Customers can request a refund within 30 days of purchase.",
        chunk_hash="second",
        metadata={"source_revision_id": str(uuid.uuid4())},
    )
    service = _service(
        session,
        conversation_repository,
        message_repository,
        CitedLLM("Customers can request a refund within 30 days of purchase. [2]"),
    )
    service._retrieval = AsyncMock()
    service._retrieval.query_embedder = None
    service._retrieval.retrieve.return_value = ContextRetrievalResult(
        chunks=[first, second], diagnostics={}
    )
    turn = await service.send_message(
        conversation.id, MessageSendRequest(content="What is the refund policy?")
    )
    answer = turn.assistant_message
    assert len(answer.citations) == 2
    assert answer.citations[1].chunk_id == second.chunk_id
    assert "[2]" in answer.content
    assert answer.metadata["evidence_summary"]["context_passages"] == 2
    assert answer.metadata["evidence_summary"]["cited_passages"] == 1
    assert answer.metadata["evidence_summary"]["factual_claims"] == 1
    assert answer.metadata["evidence_summary"]["supported_factual_claims"] == 1
    assert answer.metadata["evidence_summary"]["unsupported_factual_claims"] == 0
    assert answer.metadata["claim_verification_counts"] == {
        "factual": 1,
        "coverage_scope": 0,
        "supported": 1,
        "unverified": 0,
        "unsupported": 0,
    }
    assert answer.metadata["unsupported_claim_rate"] == 0.0
    assert answer.metadata["effective_behavior"]["response_language"] == "en"
    assert answer.metadata["evidence_gate"]["candidate_wise"]["cited_count"] == 1
    assert answer.metadata["evidence_funnel"]["cited"] == 1
    assert answer.metadata["current_authority"]["cited"]["cited_revision_count"] == 1


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


@pytest.mark.parametrize("streamed", [False, True])
@pytest.mark.parametrize("quota", [False, True, "review_timeout"])
async def test_recovery_provider_failure_is_not_a_successful_evidence_refusal(
    session, conversation_repository, message_repository, streamed, quota
):
    error_class = ProviderQuotaError if quota is True else ProviderError
    error = error_class(
        "Internal provider failure",
        provider_name="echo",
        context={"reason": "evidence_review_timeout"} if quota == "review_timeout" else {},
    )
    llm = EchoLLMProvider(model="test", provider_version="1")
    llm.generate = AsyncMock(side_effect=error)
    service = _service(session, conversation_repository, message_repository, llm)
    initial = await UnresolvedRuleRetrieval().retrieve()
    initial.diagnostics.update(index_build_id=str(uuid.uuid4()), source_metadata_generation=24)
    retrieval = AsyncMock()
    retrieval.query_embedder = None
    retrieval.retrieve.return_value = initial
    service._retrieval = retrieval
    identifier = conversation_repository.get_by_id.return_value.id
    events = []
    with pytest.raises(ServiceUnavailableError) as failure:
        if streamed:
            async for event in service.stream_message(
                identifier, MessageSendRequest(content="Calculate the current refund for 100.")
            ):
                events.append(event)
        else:
            await service.send_message(
                identifier, MessageSendRequest(content="Calculate the current refund for 100.")
            )
    assert failure.value.code == (
        "evidence_review_timeout"
        if quota == "review_timeout"
        else "llm_provider_quota_exhausted"
        if quota
        else "llm_provider_unavailable"
    )
    saved = message_repository.add.call_args_list[-1].args[0]
    assert saved.finish_reason == "error"
    assert saved.insufficient_evidence_reason is None
    assert saved.message_metadata["execution_error_code"] == error.code
    assert saved.message_metadata["evidence_funnel"]["outcome"] == "failed"
    assert saved.input_tokens is None and saved.output_tokens is None
    assert "applicable period" not in saved.content
    assert llm.generate.await_count == 1
    assert not any(event.get("stage") == "generating_answer" for event in events)


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
    done = next(item for item in events if isinstance(item, dict) and item["event"] == "done")
    assert events[0]["event"] == "progress"
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
    assert not any(isinstance(item, dict) and item["event"] == "done" for item in events)
    snapshot = service._work.snapshot()
    generation = next(
        span for span in snapshot["spans"]["items"] if span["name"] == "answer_generation"
    )
    assert generation["outcome"] == "cancelled"
    llm_calls = [call for call in snapshot["provider_calls"] if call.get("kind") == "llm"]
    assert llm_calls
    assert llm_calls[-1]["status"] == "cancelled"


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


@pytest.mark.parametrize("mode", [EvidenceGateMode.ENFORCE, EvidenceGateMode.OBSERVE])
async def test_unresolved_current_rule_cannot_escape_authority_review_through_web(
    session, conversation_repository, message_repository, conversation, mode
) -> None:
    web = FakeWebSearch()
    service = _service(
        session,
        conversation_repository,
        message_repository,
        CitedLLM("Current web guidance allows refunds within 30 days [1]."),
        chat_config=ChatConfig(
            response_mode=ResponseMode.INDEXED_THEN_WEB, evidence_gate_mode=mode
        ),
    )
    service._retrieval = UnresolvedRuleRetrieval()
    service._web_search = web
    turn = await service.send_message(
        conversation.id, MessageSendRequest(content="What is the current refund guidance?")
    )
    assert not web.calls
    assert turn.assistant_message.finish_reason == "insufficient_evidence"
    assert turn.assistant_message.metadata["evidence_gate"]["reason"] == "unresolved_authority"
    assert not turn.assistant_message.citations
    assert (
        turn.assistant_message.metadata["web_search"]["status"] == "suppressed_unresolved_authority"
    )
    assert any(notice.kind == "unresolved_authority" for notice in turn.assistant_message.notices)


@pytest.mark.parametrize("streamed", [False, True])
async def test_unresolved_rules_never_reach_generation_when_no_recovery_is_allowed(
    session, conversation_repository, message_repository, conversation, streamed
) -> None:
    service = _service(
        session,
        conversation_repository,
        message_repository,
        FailingLLM(model="test", provider_version="1"),
    )
    service._retrieval = UnresolvedRuleRetrieval()
    request = MessageSendRequest(content="What is the current refund guidance?")
    if streamed:
        events = [event async for event in service.stream_message(conversation.id, request)]
        assert events
        # The persisted message is the canonical outcome for both delivery paths.
        saved = message_repository.add.call_args_list[-1].args[0]
        assert saved.insufficient_evidence_reason == "unresolved_authority"
    else:
        turn = await service.send_message(conversation.id, request)
        assert turn.assistant_message.insufficient_evidence_reason == "unresolved_authority"
        assert turn.assistant_message.finish_reason == "insufficient_evidence"
        assert "Relevant sources were found" in turn.assistant_message.content
        assert not turn.assistant_message.citations


@pytest.mark.parametrize("store_trace", [False, True])
@pytest.mark.parametrize("coverage_complete", [True, False])
@pytest.mark.parametrize("initial_kind", ["authority", "calculation", "relevance", "current_rule"])
@pytest.mark.parametrize("missing_inputs", [[], ["Date of purchase"]])
async def test_repaired_evidence_reaches_generation_without_old_rule_or_web(
    session,
    conversation_repository,
    message_repository,
    conversation,
    store_trace,
    coverage_complete,
    initial_kind,
    missing_inputs,
) -> None:
    initial = await UnresolvedRuleRetrieval().retrieve()
    if initial_kind in {"calculation", "current_rule"}:
        relevant = await FakeRetrieval().retrieve()
        initial = ContextRetrievalResult(
            chunks=[
                replace(
                    relevant.chunks[0],
                    metadata={
                        "source_role": "reference"
                        if initial_kind == "current_rule"
                        else "supporting",
                        "source_lifecycle_status": "active",
                    },
                )
            ],
            diagnostics=relevant.diagnostics,
        )
    elif initial_kind == "relevance":
        initial = await NearMissRetrieval().retrieve()
        initial.chunks[0] = replace(
            initial.chunks[0],
            score=0.9,
            rerank_relevance_score=0.9,
            evidence_relevance_score=0.9,
            evidence_calibration_id=RERANKER_RELEVANCE_CALIBRATION_ID,
        )
        initial.diagnostics.update(index_build_id=str(uuid.uuid4()), rerank_status="applied")
    initial.diagnostics["source_metadata_generation"] = 24
    current = replace(
        initial.chunks[0],
        chunk_id=uuid.uuid4(),
        content="Current refund entitlement is 45 days for eligible purchases.",
        metadata={},
        score=0.95,
        semantic_score=0.95,
        chunk_hash="current-policy",
    )
    retrieval = AsyncMock()
    retrieval.query_embedder = None
    retrieval.retrieve.side_effect = [
        initial,
        ContextRetrievalResult(
            chunks=[current],
            diagnostics={
                "index_build_id": initial.diagnostics["index_build_id"],
                "source_metadata_generation": 24,
                "candidate_trace": [{"content": "candidate payload"}],
            },
        ),
    ]
    llm = CitedLLM("Current refund entitlement is 45 days for eligible purchases [1].")
    generate = llm.generate
    answer = await generate([], temperature=None, max_tokens=100)
    llm.generate = AsyncMock(
        side_effect=[
            replace(answer, content='{"queries":["current refund entitlement eligibility"]}'),
            replace(
                answer,
                content=json.dumps(
                    {
                        "complete": coverage_complete and not missing_inputs,
                        "missing": missing_inputs if coverage_complete else [],
                        "checks": [
                            {
                                "query_index": 0,
                                "supported": True,
                                "evidence": [
                                    {
                                        "chunk_id": str(current.chunk_id),
                                        "quote": current.content,
                                    }
                                ],
                            }
                        ],
                    }
                ),
            ),
            *(
                [
                    replace(
                        answer,
                        content='{"gaps":[{"gap_index":0,"kind":"scenario_input"}]}',
                        usage=ChatUsage(0, 0),
                    )
                ]
                if coverage_complete and missing_inputs
                else []
            ),
            answer,
        ]
    )
    service = _service(
        session,
        conversation_repository,
        message_repository,
        llm,
        chat_config=ChatConfig(
            response_mode=(
                ResponseMode.INDEXED_THEN_WEB if coverage_complete else ResponseMode.INDEXED_ONLY
            )
        ),
        store_candidate_trace=store_trace,
    )
    service._retrieval = retrieval
    web = FakeWebSearch()
    service._web_search = web
    turn = await service.send_message(
        conversation.id,
        MessageSendRequest(
            content=(
                "Calculate the refund for 100."
                if initial_kind == "calculation"
                else "What is the current refund guidance?"
            )
        ),
    )
    assert not web.calls
    if not coverage_complete:
        assert turn.assistant_message.finish_reason == "insufficient_evidence"
        assert not turn.assistant_message.citations
        assert (
            turn.assistant_message.metadata["knowledge_repair"]["status"] == "coverage_incomplete"
        )
        assert llm.generate.await_count == 2
        return
    assert turn.assistant_message.citations[0].chunk_id == current.chunk_id
    assert turn.assistant_message.input_tokens == 30
    assert turn.assistant_message.output_tokens == 15
    repair = turn.assistant_message.metadata["knowledge_repair"]
    assert repair["status"] == "recovered"
    if initial_kind == "relevance":
        assert repair["trigger"] == "relevance_recovery"
    elif initial_kind == "current_rule":
        assert repair["trigger"] == "current_rule_applicability"
    assert ("retrieval" in repair["branches"][0]) is store_trace
    assert repair["coverage"]["quotes_validated"] is True
    assert "quote" not in repair["coverage"]["checks"][0]
    messages = llm.generate.call_args_list[-1].args[0]
    prompt = "\n".join(message.content for message in messages)
    assert current.content in prompt
    assert initial.chunks[0].content not in prompt
    assert ("Unresolved scenario inputs" in prompt) == bool(missing_inputs)
    assert (
        turn.assistant_message.metadata["evidence_summary"]["input_provenance"]["unresolved_inputs"]
        == missing_inputs
    )


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


async def test_foreign_web_rule_cannot_reach_generation_despite_shared_tax_words(
    session,
    conversation_repository,
    message_repository,
    conversation,
) -> None:
    llm = CitedLLM("must not reach answer generation")
    review = await llm.generate([], temperature=None, max_tokens=100)
    llm.generate = AsyncMock(return_value=replace(review, content='{"accepted":[]}'))
    web = FakeWebSearch(
        [
            WebSearchEvidence(
                evidence_id="foreign-tax",
                title="United States investment tax rebate",
                url="https://example.test/us-tax",
                content="The current United States investment tax rebate rate is 20%.",
                retrieved_at=datetime.now(UTC),
                citation_verified=True,
            )
        ]
    )
    service = _service(
        session,
        conversation_repository,
        message_repository,
        llm,
        chat_config=ChatConfig(response_mode=ResponseMode.INDEXED_THEN_WEB),
    )
    service._retrieval = EmptyRetrieval()
    service._web_search = web
    service._domain_instructions = "This project concerns Bangladesh individual income tax."
    result = await service.send_message(
        conversation.id,
        MessageSendRequest(content="What is the current rebate rate?"),
    )
    assert "Bangladesh" in web.calls[0]
    assert llm.generate.await_count == 1  # scope review only
    answer = result.assistant_message
    assert answer.finish_reason == "insufficient_evidence"
    assert not answer.citations
    assert answer.metadata["web_search"]["scope_review"]["rejected_scope_count"] == 1


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
    policy = turn.assistant_message.metadata["response_policy"]
    assert policy["indexed_policy"]["knowledge_usable"] is True
    assert policy["web"]["requested"] is False
    assert policy["answerable_scope"]["partial"] is False


@pytest.mark.parametrize("streamed", [False, True])
@pytest.mark.parametrize(
    "case",
    [
        "indexed_only_complete",
        "indexed_then_web_incomplete",
        "indexed_then_web_unresolved",
        "indexed_then_web_observe_near_miss",
        "indexed_then_web_unavailable",
        "indexed_then_web_failed",
        "indexed_then_web_rejected",
        "indexed_then_web_scoped",
        "indexed_and_web_combined",
    ],
)
async def test_response_policy_matrix_for_streaming_and_non_streaming(
    session,
    conversation_repository,
    message_repository,
    conversation,
    streamed,
    case,
) -> None:
    conversation.system_prompt_version = "v5"
    web: FakeWebSearch | FailingWebSearch | None = FakeWebSearch()
    chat = ChatConfig(response_mode=ResponseMode.INDEXED_THEN_WEB, system_prompt_version="v5")
    retrieval: object = FakeRetrieval()
    llm: EchoLLMProvider = CitedLLM("Refunds are available within 30 days [1].")
    request = MessageSendRequest(content="What is the current refund guidance?")
    if case == "indexed_only_complete":
        chat = ChatConfig(response_mode=ResponseMode.INDEXED_ONLY, system_prompt_version="v5")
        llm = CitedLLM("Refunds are available within 30 days [1].")
        request = MessageSendRequest(content="What is the refund policy?")
    elif case == "indexed_then_web_incomplete":
        retrieval = EmptyRetrieval()
        llm = CitedLLM("Current web guidance allows refunds within 30 days [1].")
    elif case == "indexed_then_web_unresolved":
        retrieval = UnresolvedRuleRetrieval()
        llm = FailingLLM(model="test", provider_version="1")
    elif case == "indexed_then_web_observe_near_miss":
        chat = ChatConfig(
            response_mode=ResponseMode.INDEXED_THEN_WEB,
            evidence_gate_mode=EvidenceGateMode.OBSERVE,
            system_prompt_version="v5",
        )
        retrieval = NearMissRetrieval()
        llm = CitedLLM("Office stationery occupies this chapter [1].")
        request = MessageSendRequest(content="What are the source tax deduction categories?")
    elif case == "indexed_then_web_unavailable":
        retrieval = EmptyRetrieval()
        web = None
    elif case == "indexed_then_web_failed":
        retrieval = EmptyRetrieval()
        web = FailingWebSearch()
    elif case == "indexed_then_web_rejected":
        retrieval = EmptyRetrieval()
        llm = CitedLLM("must not run")
        web = FakeWebSearch(
            [
                WebSearchEvidence(
                    evidence_id="uncited",
                    title="Refund policy",
                    url="https://example.test/refunds",
                    content="Refunds are available within 30 days.",
                    retrieved_at=datetime.now(UTC),
                    citation_verified=False,
                )
            ]
        )
    elif case == "indexed_then_web_scoped":
        retrieval = EmptyRetrieval()
        request = MessageSendRequest(
            content="What is the current refund guidance?",
            document_id=uuid.uuid4(),
        )
    else:
        chat = ChatConfig(response_mode=ResponseMode.INDEXED_AND_WEB, system_prompt_version="v5")
        llm = CitedLLM("Refunds are available within 30 days [1].")
        request = MessageSendRequest(content="What is the refund policy?")
    service = _service(
        session,
        conversation_repository,
        message_repository,
        llm,
        chat_config=chat,
    )
    service._retrieval = retrieval
    service._web_search = web
    if streamed:
        events = [event async for event in service.stream_message(conversation.id, request)]
        assert events
        saved = message_repository.add.call_args_list[-1].args[0]
        metadata = saved.message_metadata or {}
    else:
        turn = await service.send_message(conversation.id, request)
        metadata = turn.assistant_message.metadata
    policy = metadata["response_policy"]
    assert policy["response_mode"] == chat.response_mode.value
    assert policy["gate_mode"] == chat.evidence_gate_mode.value
    if case == "indexed_only_complete":
        assert policy["indexed_policy"]["knowledge_usable"] is True
        assert policy["web"]["requested"] is False
        assert policy["web"]["status"] == "not_requested"
    elif case == "indexed_then_web_incomplete":
        assert policy["indexed_policy"]["knowledge_usable"] is False
        assert policy["web"]["requested"] is True
        assert policy["web"]["fallback_used"] is True
        assert policy["web"]["status"] == "evidence_accepted"
        assert policy["answerable_scope"]["complete"] is False
    elif case == "indexed_then_web_unresolved":
        assert policy["unresolved_authority"] is True
        assert policy["web"]["status"] == "suppressed_unresolved_authority"
        assert policy["web"]["requested"] is True
        assert policy["indexed_policy"]["blocks_generation"] is True
    elif case == "indexed_then_web_observe_near_miss":
        assert policy["gate_mode"] == EvidenceGateMode.OBSERVE.value
        assert policy["indexed_policy"]["knowledge_usable"] is True
        assert policy["web"]["requested"] is False
    elif case == "indexed_then_web_unavailable":
        assert policy["web"]["status"] == "provider_unavailable"
        assert policy["web"]["fallback_used"] is False
    elif case == "indexed_then_web_failed":
        assert policy["web"]["status"] == "failed"
        assert policy["web"]["fallback_used"] is False
    elif case == "indexed_then_web_rejected":
        assert policy["web"]["status"] == "evidence_extracted_irrelevant"
        assert policy["web"]["fallback_used"] is False
        assert policy["indexed_policy"]["knowledge_usable"] is False
    elif case == "indexed_then_web_scoped":
        assert policy["scoped_request"] is True
        assert policy["web"]["status"] == "suppressed_scoped_request"
    else:
        assert policy["response_mode"] == ResponseMode.INDEXED_AND_WEB.value
        assert policy["web"]["requested"] is True
        assert policy["indexed_policy"]["knowledge_usable"] is True


@pytest.mark.parametrize("streamed", [False, True])
@pytest.mark.parametrize("mode", [ResponseMode.INDEXED_ONLY, ResponseMode.INDEXED_THEN_WEB])
async def test_validated_partial_cannot_alter_indexed_web_policy(
    session,
    conversation_repository,
    message_repository,
    conversation,
    streamed,
    mode,
) -> None:
    initial = await UnresolvedRuleRetrieval().retrieve()
    initial.diagnostics["source_metadata_generation"] = 24
    current = replace(
        initial.chunks[0],
        chunk_id=uuid.uuid4(),
        content="Current refund entitlement is 45 days for eligible purchases.",
        metadata={},
        score=0.95,
        semantic_score=0.95,
        chunk_hash="current-policy",
    )
    retrieval = AsyncMock()
    retrieval.query_embedder = None
    retrieval.retrieve.side_effect = [
        initial,
        ContextRetrievalResult(
            chunks=[current],
            diagnostics={
                "index_build_id": initial.diagnostics["index_build_id"],
                "source_metadata_generation": 24,
            },
        ),
    ]
    llm = CitedLLM("Current refund entitlement is 45 days for eligible purchases [1].")
    generate = llm.generate
    answer = await generate([], temperature=None, max_tokens=100)
    llm.generate = AsyncMock(
        side_effect=[
            replace(
                answer,
                content=json.dumps(
                    {
                        "queries": [
                            {
                                "query": "current refund entitlement eligibility",
                                "requirement_ids": ["R1", "R2"],
                            }
                        ],
                        "requirements": [
                            {
                                "requirement_id": "R1",
                                "description": "Filing duty",
                            },
                            {
                                "requirement_id": "R2",
                                "description": "Refund period",
                            },
                        ],
                    }
                ),
            ),
            replace(
                answer,
                content=json.dumps(
                    {
                        "complete": False,
                        "missing": ["Filing duty"],
                        "checks": [
                            {
                                "requirement_id": "R1",
                                "description": "Filing duty",
                                "supported": False,
                                "evidence": [],
                            },
                            {
                                "requirement_id": "R2",
                                "description": "Refund period",
                                "supported": True,
                                "evidence": [
                                    {
                                        "chunk_id": str(current.chunk_id),
                                        "quote": current.content,
                                    }
                                ],
                            },
                        ],
                        "partial_answer": {
                            "scope": "Refund period",
                            "requirement_ids": ["R2"],
                            "exclusions": ["Filing duty"],
                        },
                    }
                ),
            ),
            replace(answer, content=json.dumps({"queries": []})),
            answer,
        ]
    )
    web = FakeWebSearch()
    service = _service(
        session,
        conversation_repository,
        message_repository,
        llm,
        chat_config=ChatConfig(response_mode=mode, system_prompt_version="v5"),
    )
    service._retrieval = retrieval
    service._web_search = web
    request = MessageSendRequest(content="What is the current refund guidance?")
    if streamed:
        events = [event async for event in service.stream_message(conversation.id, request)]
        assert events
        saved = message_repository.add.call_args_list[-1].args[0]
        metadata = saved.message_metadata or {}
        content = saved.content
    else:
        turn = await service.send_message(conversation.id, request)
        metadata = turn.assistant_message.metadata
        content = turn.assistant_message.content
    assert not web.calls
    policy = metadata["response_policy"]
    assert policy["indexed_policy"]["knowledge_usable"] is True
    assert policy["indexed_policy"]["sufficient"] is True
    assert policy["web"]["requested"] is False
    assert policy["answerable_scope"]["complete"] is False
    assert policy["answerable_scope"]["partial"] is True
    assert metadata["knowledge_repair"]["status"] == "partial_answer"
    assert "independently supported" in content or "45 days" in content


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


def test_scope_notice_uses_inherited_document_id() -> None:
    inherited = uuid.uuid4()
    status = _scope_current_authority_status(
        MessageSendRequest(content="Make it shorter."),
        {
            "modifies_expansion_status": "suppressed_document_scope",
            "modifies_expansion_records": [
                {
                    "relationship_type": "modifies",
                    "modifier_effective_from": "2020-01-01",
                    "outcome": "expanded",
                }
            ],
        },
        document_id=inherited,
    )
    assert status is not None
    assert status["status"] == "effective_modifier_excluded_by_scope"


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


def _reusable_chunk(
    content: str = "A customer may request a refund within 30 days of purchase.",
) -> ContextChunk:
    raw_hash = content_hash(content)
    return ContextChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=1,
        content=content,
        score=0.9,
        filename="policy.txt",
        chunk_hash=raw_hash,
        semantic_score=0.9,
        char_start=0,
        char_end=len(content),
        metadata={
            "indexed_chunk_hash": raw_hash,
            "configuration_hash": "a" * 64,
            "index_build_id": str(uuid.uuid4()),
            "source_metadata_generation": 1,
            "processing_version": 1,
        },
    )


def _reusable_citation(chunk: ContextChunk, **overrides: object) -> dict[str, object]:
    span_hash = content_hash(chunk.content)
    citation: dict[str, object] = {
        "source_kind": "knowledge",
        "chunk_id": str(chunk.chunk_id),
        "document_id": str(chunk.document_id),
        "filename": chunk.filename,
        "evidence_provenance_version": EVIDENCE_PROVENANCE_VERSION,
        "indexed_chunk_hash": chunk.metadata["indexed_chunk_hash"],
        "evidence_source_chunk_hash": chunk.metadata["indexed_chunk_hash"],
        "evidence_span_hash": span_hash,
        "evidence_chunk_char_start": 0,
        "evidence_chunk_char_end": len(chunk.content),
        "evidence_span_derivation": "complete_chunk",
        "evidence_corroboration_method": "original_lexical",
        "evidence_source_envelope": "contiguous_span",
        "configuration_hash": chunk.metadata.get("configuration_hash"),
        "chunk_hash": span_hash,
        "evidence_unit_id": str(uuid.uuid4()),
    }
    citation.update(overrides)
    return citation


_RUNTIME_IDENTITY_KEYS = (
    "retrieval_scope",
    "relationship_recall_provenance",
    "relationship_grounding_trust",
)


def _identity_recalled_chunk(chunk: ContextChunk) -> ContextChunk:
    return replace(
        chunk,
        metadata={
            key: value for key, value in chunk.metadata.items() if key not in _RUNTIME_IDENTITY_KEYS
        },
    )


class ExactRecallRetrieval:
    supports_cited_retrieval = True
    supports_exact_recall = True

    def __init__(
        self,
        chunk: ContextChunk,
        *,
        extras: list[ContextChunk] | None = None,
        configuration_hash: str | None = None,
        expansion_records: list[dict[str, object]] | None = None,
    ) -> None:
        self.chunk = chunk
        self.extras = extras or []
        self.expansion_records = expansion_records or []
        self.retrieve_calls: list[dict[str, object]] = []
        self.exact_calls: list[dict[str, object]] = []
        self._configuration_hash = configuration_hash or chunk.metadata.get("configuration_hash")

    def _requested_chunks(self, kwargs: dict[str, object]) -> list[ContextChunk]:
        requested = kwargs.get("chunk_ids") or []
        available = [self.chunk, *self.extras]
        if not isinstance(requested, list) or not requested:
            return available
        by_id = {chunk.chunk_id: chunk for chunk in available}
        return [by_id[item] for item in requested if item in by_id]

    def _identity_diagnostics(self, kwargs: dict[str, object], count: int) -> dict[str, object]:
        if kwargs.get("document_id") is not None:
            status = "suppressed_document_scope"
        elif self.expansion_records:
            status = "observe"
        else:
            status = "no_relationships"
        return {
            "retrieved_candidate_count": count,
            "rerank_status": "skipped",
            "configuration_hash": self._configuration_hash,
            "modifies_expansion_records": list(self.expansion_records),
            "modifies_expansion_status": status,
        }

    async def retrieve(self, **kwargs: object) -> ContextRetrievalResult:
        work = current_request_work()
        if work is not None:
            work.counts["ranked_retrieval_calls"] += 1
        self.retrieve_calls.append(kwargs)
        return ContextRetrievalResult(
            chunks=[self.chunk],
            diagnostics={"retrieved_candidate_count": 1, "rerank_status": "skipped"},
        )

    async def retrieve_exact(self, **kwargs: object) -> ContextRetrievalResult:
        self.exact_calls.append(kwargs)
        chunks = [_identity_recalled_chunk(item) for item in self._requested_chunks(kwargs)]
        return ContextRetrievalResult(
            chunks=chunks,
            diagnostics=self._identity_diagnostics(kwargs, len(chunks)),
        )


async def test_explicit_presentation_rewrite_reuses_only_visible_prior_citations(
    session,
    conversation_repository,
    message_repository,
    conversation,
):
    cited_chunk = _reusable_chunk()
    prior_user, prior_assistant = _history_messages(
        conversation,
        user_content="What is the refund period?",
        assistant_content="A request may be made within 30 days of purchase. [2]",
    )
    prior_assistant.citations = [
        {
            "source_kind": "knowledge",
            "chunk_id": str(uuid.uuid4()),
            "document_id": str(uuid.uuid4()),
            "filename": "nearby.txt",
        },
        _reusable_citation(cited_chunk),
    ]
    message_repository.list_recent_for_conversation.return_value = [
        prior_user,
        prior_assistant,
    ]
    retrieval = ExactRecallRetrieval(cited_chunk)
    llm = ScriptedResolutionLLM(
        _resolved_payload(
            relation="standalone",
            effective_question="Rewrite that as exactly three short bullets in English.",
        ),
        answer=(
            "- Refunds may be requested. [1]\n"
            "- The period is 30 days. [1]\n"
            "- It runs from purchase. [1]"
        ),
    )
    service = _service(session, conversation_repository, message_repository, llm)
    service._retrieval = retrieval

    turn = await service.send_message(
        conversation.id,
        MessageSendRequest(
            content=(
                "Rewrite that as exactly three short bullets in English. "
                "Keep the same facts and citations."
            )
        ),
    )

    assert retrieval.exact_calls and not retrieval.retrieve_calls
    assert retrieval.exact_calls[0]["chunk_ids"] == [cited_chunk.chunk_id]
    counts = turn.assistant_message.metadata["lifecycle"]["counts"]
    assert counts.get("resolver_calls", 0) == 0
    assert counts.get("ranked_retrieval_calls", 0) == 0
    assert counts.get("translation_calls", 0) == 0
    assert counts.get("rerank_calls", 0) == 0
    assert counts.get("planner_calls", 0) == 0
    assert counts.get("coverage_review_calls", 0) == 0
    assert counts.get("recovery_attempts", 0) == 0
    assert llm.resolver_calls == 0
    assert llm.generate_calls == 1
    metadata = turn.assistant_message.metadata
    assert metadata["evidence_gate"]["passage_rescue"]["candidate_count"] == 0
    assert metadata["evidence_gate"]["candidate_wise"]["path"] == "exact_citation_recall"
    assert metadata["rewrite_recall"]["status"] == "exact_cited_passages"
    assert metadata["knowledge_repair"]["status"] == "not_needed"
    assert metadata["turn_resolution"]["routing_origin"] == "deterministic"
    assert metadata["turn_resolution"]["followup_mode"] == "presentation_only"
    assert metadata["evidence_summary"]["coverage_method"] == ("current_exact_citation_recall")
    assert metadata["evidence_summary"]["admitted_passages"] == 0
    assert metadata["evidence_summary"]["reused_cited_passages"] == 1
    assert metadata["presentation_reuse"]["reuse_validation_outcome"] == "passed"
    assert turn.assistant_message.citations[0].chunk_id == cited_chunk.chunk_id
    assert "Preserve material meaning naturally" in llm.generation_prompts[0][0].content
    assert metadata["evidence_summary"]["claim_verification"] in {"verified", "unverified"}
    assert metadata["evidence_summary"]["supported_factual_claims"] >= 1
    assert turn.assistant_message.content.count("- ") == 3


async def test_missing_provenance_falls_back_to_ranked_cited_recall(
    session,
    conversation_repository,
    message_repository,
    conversation,
):
    cited_chunk = _reusable_chunk()
    prior_user, prior_assistant = _history_messages(
        conversation,
        user_content="What is the refund period?",
        assistant_content="A request may be made within 30 days of purchase. [1]",
    )
    prior_assistant.citations = [
        {
            "source_kind": "knowledge",
            "chunk_id": str(cited_chunk.chunk_id),
            "document_id": str(cited_chunk.document_id),
            "filename": cited_chunk.filename,
        }
    ]
    message_repository.list_recent_for_conversation.return_value = [prior_user, prior_assistant]
    retrieval = ExactRecallRetrieval(cited_chunk)
    llm = ScriptedResolutionLLM({}, answer="Refunds may be requested within 30 days. [1]")
    service = _service(session, conversation_repository, message_repository, llm)
    service._retrieval = retrieval
    turn = await service.send_message(
        conversation.id, MessageSendRequest(content="Make it shorter.")
    )
    assert not retrieval.exact_calls
    assert retrieval.retrieve_calls
    assert turn.assistant_message.metadata["presentation_reuse"]["status"] == "fallback"
    assert turn.assistant_message.metadata["presentation_reuse"]["reuse_failure_category"] == (
        "missing_provenance"
    )
    assert llm.resolver_calls == 0
    assert (
        turn.assistant_message.metadata["lifecycle"]["counts"].get("ranked_retrieval_calls", 0) == 1
    )


async def test_second_presentation_rewrite_stays_on_exact_recall(
    session,
    conversation_repository,
    message_repository,
    conversation,
):
    cited_chunk = _reusable_chunk()
    first_user, first_assistant = _history_messages(
        conversation,
        user_content="What is the refund period?",
        assistant_content="A request may be made within 30 days of purchase. [1]",
    )
    rewrite_user, rewrite_assistant = _history_messages(
        conversation,
        user_content="Rewrite that as exactly three short bullets in English.",
        assistant_content="- Refunds may be requested. [1]\n- The period is 30 days. [1]",
    )
    first_assistant.citations = [_reusable_citation(cited_chunk)]
    rewrite_assistant.citations = [_reusable_citation(cited_chunk)]
    message_repository.list_recent_for_conversation.return_value = [
        first_user,
        first_assistant,
        rewrite_user,
        rewrite_assistant,
    ]
    retrieval = ExactRecallRetrieval(cited_chunk)
    llm = ScriptedResolutionLLM({}, answer="Refunds are available within 30 days. [1]")
    service = _service(session, conversation_repository, message_repository, llm)
    service._retrieval = retrieval
    turn = await service.send_message(
        conversation.id, MessageSendRequest(content="Make it shorter.")
    )
    assert retrieval.exact_calls and not retrieval.retrieve_calls
    assert llm.generate_calls == 1 and llm.resolver_calls == 0
    assert turn.assistant_message.metadata["evidence_summary"]["coverage_method"] == (
        "current_exact_citation_recall"
    )


async def test_mixed_web_citations_do_not_qualify_indexed_only_reuse(
    session,
    conversation_repository,
    message_repository,
    conversation,
):
    cited_chunk = _reusable_chunk()
    prior_user, prior_assistant = _history_messages(
        conversation,
        user_content="What is the refund period?",
        assistant_content="Indexed fact [1] and a public note [2].",
    )
    prior_assistant.citations = [
        _reusable_citation(cited_chunk),
        {
            "source_kind": "web",
            "filename": "Refund policy",
            "web_url": "https://example.test/refunds",
            "web_title": "Refund policy",
            "web_retrieved_at": datetime(2026, 7, 1, tzinfo=UTC).isoformat(),
            "web_provider": "test_web",
        },
    ]
    message_repository.list_recent_for_conversation.return_value = [prior_user, prior_assistant]
    retrieval = ExactRecallRetrieval(cited_chunk)
    llm = ScriptedResolutionLLM(
        _resolved_payload(relation="follow_up", effective_question="Make it shorter."),
        answer="Refunds are available within 30 days. [1]",
    )
    service = _service(session, conversation_repository, message_repository, llm)
    service._retrieval = retrieval
    turn = await service.send_message(
        conversation.id, MessageSendRequest(content="Make it shorter.")
    )
    assert llm.resolver_calls == 0
    assert llm.generate_calls == 1
    assert not retrieval.exact_calls
    assert retrieval.retrieve_calls
    assert turn.assistant_message.metadata["turn_resolution"]["routing_origin"] == ("deterministic")
    assert turn.assistant_message.metadata.get("presentation_reuse", {}).get("status") != ("reused")
    assert turn.assistant_message.metadata["presentation_reuse"]["reuse_failure_category"] == (
        "mixed_web"
    )


async def test_indexed_and_web_mixed_citations_reuse_indexed_branch_and_keep_web(
    session,
    conversation_repository,
    message_repository,
    conversation,
):
    cited_chunk = _reusable_chunk()
    prior_user, prior_assistant = _history_messages(
        conversation,
        user_content="What is the refund period?",
        assistant_content="Indexed fact [1] and a public note [2].",
    )
    prior_assistant.citations = [
        _reusable_citation(cited_chunk),
        {
            "source_kind": "web",
            "filename": "Refund policy",
            "web_url": "https://example.test/refunds",
            "web_title": "Refund policy",
            "web_retrieved_at": datetime(2026, 7, 1, tzinfo=UTC).isoformat(),
            "web_provider": "test_web",
        },
    ]
    message_repository.list_recent_for_conversation.return_value = [prior_user, prior_assistant]
    retrieval = ExactRecallRetrieval(cited_chunk)
    llm = ScriptedResolutionLLM({}, answer="Refunds are available within 30 days. [1]")
    service = _service(
        session,
        conversation_repository,
        message_repository,
        llm,
        chat_config=ChatConfig(
            system_prompt_version="v1",
            response_mode=ResponseMode.INDEXED_AND_WEB,
        ),
    )
    service._retrieval = retrieval
    service._web_search = FakeWebSearch()
    turn = await service.send_message(
        conversation.id, MessageSendRequest(content="Make it shorter.")
    )
    assert retrieval.exact_calls and not retrieval.retrieve_calls
    assert service._web_search.calls
    assert llm.resolver_calls == 0
    assert (
        turn.assistant_message.metadata["lifecycle"]["counts"].get("ranked_retrieval_calls", 0) == 0
    )
    assert turn.assistant_message.metadata["presentation_reuse"]["status"] == "reused"
    assert turn.assistant_message.metadata["web_search"]["status"] != "not_requested"


async def test_changed_request_document_scope_exits_exact_reuse(
    session,
    conversation_repository,
    message_repository,
    conversation,
):
    cited_chunk = _reusable_chunk()
    prior_user, prior_assistant = _history_messages(
        conversation,
        user_content="What is the refund period?",
        assistant_content="A request may be made within 30 days of purchase. [1]",
    )
    prior_assistant.citations = [
        _reusable_citation(
            cited_chunk,
            evidence_scope_document_id=str(cited_chunk.document_id),
            evidence_scope_metadata_filter={},
        )
    ]
    message_repository.list_recent_for_conversation.return_value = [prior_user, prior_assistant]
    retrieval = ExactRecallRetrieval(cited_chunk)
    llm = ScriptedResolutionLLM({}, answer="Refunds are available within 30 days. [1]")
    service = _service(session, conversation_repository, message_repository, llm)
    service._retrieval = retrieval
    turn = await service.send_message(
        conversation.id,
        MessageSendRequest(content="Make it shorter.", document_id=uuid.uuid4()),
    )
    assert llm.resolver_calls == 0
    assert not retrieval.exact_calls
    assert retrieval.retrieve_calls
    assert turn.assistant_message.metadata["presentation_reuse"]["reuse_failure_category"] == (
        "request_scope_changed"
    )


async def test_inherited_historical_scope_is_passed_to_exact_recall(
    session,
    conversation_repository,
    message_repository,
    conversation,
):
    cited_chunk = _reusable_chunk()
    as_of = datetime(2025, 6, 1, tzinfo=UTC)
    prior_user, prior_assistant = _history_messages(
        conversation,
        user_content="What rules applied on 2025-06-01?",
        assistant_content="The 2025 edition required filing within 30 days. [1]",
    )
    prior_assistant.citations = [
        _reusable_citation(
            cited_chunk,
            evidence_scope_as_of=as_of.isoformat(),
            evidence_scope_snapshot_origin="user_literal",
        )
    ]
    message_repository.list_recent_for_conversation.return_value = [prior_user, prior_assistant]
    retrieval = ExactRecallRetrieval(cited_chunk)
    llm = ScriptedResolutionLLM({}, answer="Filing was required within 30 days. [1]")
    service = _service(
        session,
        conversation_repository,
        message_repository,
        llm,
        chat_config=ChatConfig(
            system_prompt_version="v1",
            response_mode=ResponseMode.INDEXED_THEN_WEB,
        ),
    )
    service._retrieval = retrieval
    service._web_search = FakeWebSearch()
    turn = await service.send_message(
        conversation.id, MessageSendRequest(content="Make it shorter.")
    )
    assert retrieval.exact_calls[0]["as_of"] == as_of
    assert not service._web_search.calls
    assert turn.assistant_message.metadata["web_search"]["status"] in {
        "not_requested",
        "suppressed_scoped_request",
    }
    assert turn.assistant_message.metadata["presentation_reuse"]["status"] == "reused"


async def test_configuration_change_invalidates_exact_reuse(
    session,
    conversation_repository,
    message_repository,
    conversation,
):
    cited_chunk = _reusable_chunk()
    prior_user, prior_assistant = _history_messages(
        conversation,
        user_content="What is the refund period?",
        assistant_content="A request may be made within 30 days of purchase. [1]",
    )
    prior_assistant.citations = [_reusable_citation(cited_chunk)]
    message_repository.list_recent_for_conversation.return_value = [prior_user, prior_assistant]
    retrieval = ExactRecallRetrieval(cited_chunk, configuration_hash="z" * 64)
    llm = ScriptedResolutionLLM({}, answer="Refunds are available within 30 days. [1]")
    service = _service(session, conversation_repository, message_repository, llm)
    service._retrieval = retrieval
    turn = await service.send_message(
        conversation.id, MessageSendRequest(content="Make it shorter.")
    )
    assert retrieval.exact_calls and retrieval.retrieve_calls
    assert turn.assistant_message.metadata["presentation_reuse"]["reuse_failure_category"] == (
        "configuration_changed"
    )


async def test_bangla_presentation_rewrite_uses_exact_recall(
    session,
    conversation_repository,
    message_repository,
    conversation,
):
    cited_chunk = _reusable_chunk()
    prior_user, prior_assistant = _history_messages(
        conversation,
        user_content="ফেরতের সময়সীমা কত?",
        assistant_content="ক্রয়ের ৩০ দিনের মধ্যে ফেরত চাওয়া যায়। [1]",
    )
    prior_assistant.citations = [_reusable_citation(cited_chunk)]
    message_repository.list_recent_for_conversation.return_value = [prior_user, prior_assistant]
    retrieval = ExactRecallRetrieval(cited_chunk)
    llm = ScriptedResolutionLLM(
        {},
        answer="- ফেরত চাওয়া যায়। [1]\n- সময়সীমা ৩০ দিন। [1]\n- ক্রয় থেকে গণনা হয়। [1]",
    )
    service = _service(session, conversation_repository, message_repository, llm)
    service._retrieval = retrieval
    turn = await service.send_message(
        conversation.id,
        MessageSendRequest(content="আগের উত্তরটি সহজ বাংলায় তিনটি বুলেটে বলুন। নতুন তথ্য যোগ করবেন না।"),
    )
    assert retrieval.exact_calls and not retrieval.retrieve_calls
    assert llm.resolver_calls == 0
    assert llm.generate_calls == 1
    assert turn.assistant_message.metadata["turn_resolution"]["followup_mode"] == (
        "presentation_only"
    )
    assert turn.assistant_message.metadata["evidence_summary"]["coverage_method"] == (
        "current_exact_citation_recall"
    )


async def test_mixed_web_indexed_and_web_keeps_exact_indexed_branch_and_web_workflow(
    session,
    conversation_repository,
    message_repository,
    conversation,
):
    cited_chunk = _reusable_chunk()
    prior_user, prior_assistant = _history_messages(
        conversation,
        user_content="What is the refund period?",
        assistant_content="Indexed fact [1] and a public note [2].",
    )
    prior_assistant.citations = [
        _reusable_citation(cited_chunk),
        {
            "source_kind": "web",
            "filename": "Refund policy",
            "web_url": "https://example.test/refunds",
            "web_title": "Refund policy",
            "web_retrieved_at": datetime(2026, 7, 1, tzinfo=UTC).isoformat(),
            "web_provider": "test_web",
        },
    ]
    message_repository.list_recent_for_conversation.return_value = [prior_user, prior_assistant]
    retrieval = ExactRecallRetrieval(cited_chunk)
    llm = ScriptedResolutionLLM({}, answer="Refunds are available within 30 days. [1]")
    service = _service(
        session,
        conversation_repository,
        message_repository,
        llm,
        chat_config=ChatConfig(
            system_prompt_version="v1",
            response_mode=ResponseMode.INDEXED_AND_WEB,
        ),
    )
    service._retrieval = retrieval
    service._web_search = FakeWebSearch([])
    turn = await service.send_message(
        conversation.id, MessageSendRequest(content="Make it shorter.")
    )
    assert retrieval.exact_calls and not retrieval.retrieve_calls
    assert service._web_search.calls
    assert llm.resolver_calls == 0
    assert turn.assistant_message.metadata["presentation_reuse"]["status"] == "reused"
    assert turn.assistant_message.metadata["web_search"]["status"] != "not_requested"


async def test_adapter_without_exact_recall_falls_back_to_cited_retrieve(
    session,
    conversation_repository,
    message_repository,
    conversation,
):
    cited_chunk = _reusable_chunk()
    prior_user, prior_assistant = _history_messages(
        conversation,
        user_content="What is the refund period?",
        assistant_content="A request may be made within 30 days of purchase. [1]",
    )
    prior_assistant.citations = [_reusable_citation(cited_chunk)]
    message_repository.list_recent_for_conversation.return_value = [prior_user, prior_assistant]

    class CitedOnlyRetrieval:
        supports_cited_retrieval = True
        supports_exact_recall = False

        def __init__(self) -> None:
            self.retrieve_calls: list[dict[str, object]] = []

        async def retrieve(self, **kwargs: object) -> ContextRetrievalResult:
            work = current_request_work()
            if work is not None:
                work.counts["ranked_retrieval_calls"] += 1
            self.retrieve_calls.append(kwargs)
            return ContextRetrievalResult(
                chunks=[cited_chunk],
                diagnostics={"retrieved_candidate_count": 1, "rerank_status": "skipped"},
            )

    retrieval = CitedOnlyRetrieval()
    llm = ScriptedResolutionLLM({}, answer="Refunds are available within 30 days. [1]")
    service = _service(session, conversation_repository, message_repository, llm)
    service._retrieval = retrieval
    turn = await service.send_message(
        conversation.id, MessageSendRequest(content="Make it shorter.")
    )
    assert retrieval.retrieve_calls
    assert "cited_chunk_ids" in retrieval.retrieve_calls[0]
    assert llm.resolver_calls == 0
    assert turn.assistant_message.metadata.get("presentation_reuse", {}).get("status") != "reused"
    assert turn.assistant_message.metadata["presentation_reuse"]["reuse_failure_category"] == (
        "exact_recall_unsupported"
    )
    assert (
        turn.assistant_message.metadata["lifecycle"]["counts"].get("ranked_retrieval_calls", 0) == 1
    )


async def test_missing_identity_and_unrecorded_modifier_fall_back_from_exact_recall(
    session,
    conversation_repository,
    message_repository,
    conversation,
):
    cited_chunk = _reusable_chunk()
    prior_user, prior_assistant = _history_messages(
        conversation,
        user_content="What is the refund period?",
        assistant_content="A request may be made within 30 days of purchase. [1]",
    )
    prior_assistant.citations = [_reusable_citation(cited_chunk)]
    message_repository.list_recent_for_conversation.return_value = [prior_user, prior_assistant]
    retrieval = ExactRecallRetrieval(cited_chunk)
    retrieval.retrieve_exact = AsyncMock(  # type: ignore[method-assign]
        return_value=ContextRetrievalResult(
            chunks=[],
            diagnostics={"configuration_hash": cited_chunk.metadata["configuration_hash"]},
        )
    )
    llm = ScriptedResolutionLLM({}, answer="Refunds are available within 30 days. [1]")
    service = _service(session, conversation_repository, message_repository, llm)
    service._retrieval = retrieval
    missing = await service.send_message(
        conversation.id, MessageSendRequest(content="Make it shorter.")
    )
    assert retrieval.retrieve_calls
    assert missing.assistant_message.metadata["presentation_reuse"]["reuse_failure_category"] == (
        "missing_identity"
    )

    modifier_chunk = replace(
        cited_chunk,
        metadata={
            **cited_chunk.metadata,
            "source_revision_id": str(uuid.uuid4()),
            "source_relationships": [
                {
                    "relationship_type": "modifies",
                    "direction": "incoming",
                    "source_revision_id": str(uuid.uuid4()),
                }
            ],
        },
    )
    retrieval = ExactRecallRetrieval(modifier_chunk)
    service._retrieval = retrieval
    authority = await service.send_message(
        conversation.id, MessageSendRequest(content="Make it shorter.")
    )
    assert retrieval.exact_calls and retrieval.retrieve_calls
    assert authority.assistant_message.metadata["presentation_reuse"]["reuse_failure_category"] == (
        "unrecorded_authority_dependency"
    )


async def test_applicability_update_does_not_use_deterministic_exact_reuse(
    session,
    conversation_repository,
    message_repository,
    conversation,
):
    cited_chunk = _reusable_chunk()
    prior_user, prior_assistant = _history_messages(
        conversation,
        user_content="What is the refund period?",
        assistant_content="A request may be made within 30 days of purchase. [1]",
    )
    prior_assistant.citations = [_reusable_citation(cited_chunk)]
    message_repository.list_recent_for_conversation.return_value = [prior_user, prior_assistant]
    retrieval = ExactRecallRetrieval(cited_chunk)
    llm = ScriptedResolutionLLM(
        _resolved_payload(relation="follow_up", effective_question="Do these rules still apply?"),
        answer="The selected evidence did not establish current applicability.",
    )
    service = _service(session, conversation_repository, message_repository, llm)
    service._retrieval = retrieval
    await service.send_message(
        conversation.id, MessageSendRequest(content="Do these rules still apply?")
    )
    assert llm.resolver_calls == 1
    assert not retrieval.exact_calls
    assert retrieval.retrieve_calls


@pytest.mark.parametrize(
    ("question", "relation"),
    [
        ("Summarize penalties", "topic_change"),
        ("Rewrite it for minors", "follow_up"),
        ("Summarize it for partnerships", "follow_up"),
    ],
)
async def test_short_fact_or_population_change_uses_resolver_and_ranked_retrieval(
    session,
    conversation_repository,
    message_repository,
    conversation,
    question: str,
    relation: str,
):
    cited_chunk = _reusable_chunk()
    prior_user, prior_assistant = _history_messages(
        conversation,
        user_content="What is the refund period?",
        assistant_content="A request may be made within 30 days. [1]",
    )
    prior_assistant.citations = [_reusable_citation(cited_chunk)]
    message_repository.list_recent_for_conversation.return_value = [prior_user, prior_assistant]
    retrieval = ExactRecallRetrieval(cited_chunk)
    llm = ScriptedResolutionLLM(
        _resolved_payload(relation=relation, effective_question=question),
        answer="The selected evidence supports this answer. [1]",
    )
    service = _service(session, conversation_repository, message_repository, llm)
    service._retrieval = retrieval

    await service.send_message(conversation.id, MessageSendRequest(content=question))

    assert llm.resolver_calls == 1
    assert not retrieval.exact_calls
    assert len(retrieval.retrieve_calls) == 1
    assert retrieval.retrieve_calls[0]["query"] == question
    assert "cited_chunk_ids" not in retrieval.retrieve_calls[0]


async def test_ordinary_followup_does_not_seed_cited_retrieval(
    session,
    conversation_repository,
    message_repository,
    conversation,
):
    cited_chunk = _reusable_chunk()
    prior_user, prior_assistant = _history_messages(
        conversation,
        user_content="What is the refund period?",
        assistant_content="A request may be made within 30 days of purchase. [1]",
    )
    prior_assistant.citations = [_reusable_citation(cited_chunk)]
    message_repository.list_recent_for_conversation.return_value = [prior_user, prior_assistant]
    retrieval = ExactRecallRetrieval(cited_chunk)
    llm = ScriptedResolutionLLM(
        _resolved_payload(
            relation="standalone",
            effective_question="How much is the late filing penalty?",
        ),
        answer="The selected evidence did not establish a late filing penalty.",
    )
    service = _service(session, conversation_repository, message_repository, llm)
    service._retrieval = retrieval
    turn = await service.send_message(
        conversation.id, MessageSendRequest(content="How much is the late filing penalty?")
    )
    assert llm.resolver_calls == 1
    assert not retrieval.exact_calls
    assert len(retrieval.retrieve_calls) == 1
    assert "cited_chunk_ids" not in retrieval.retrieve_calls[0]
    assert (
        turn.assistant_message.metadata["lifecycle"]["counts"].get("ranked_retrieval_calls", 0) == 1
    )


async def test_ranked_cited_fallback_admits_chunk_when_relevance_rejects(
    session,
    conversation_repository,
    message_repository,
    conversation,
    monkeypatch,
):
    from app.modules.conversations.grounding_service import EvidenceDecision
    from app.modules.conversations.schemas.message import InsufficientEvidenceReason
    from app.modules.conversations.services import chat_service as chat_service_mod

    cited_chunk = _reusable_chunk()
    prior_user, prior_assistant = _history_messages(
        conversation,
        user_content="What is the refund period?",
        assistant_content="A request may be made within 30 days of purchase. [1]",
    )
    prior_assistant.citations = [
        {
            "source_kind": "knowledge",
            "chunk_id": str(cited_chunk.chunk_id),
            "document_id": str(cited_chunk.document_id),
            "filename": cited_chunk.filename,
        }
    ]
    message_repository.list_recent_for_conversation.return_value = [prior_user, prior_assistant]

    async def reject_relevance(**kwargs: object):
        del kwargs
        return (
            EvidenceDecision(
                sufficient=False,
                reason=InsufficientEvidenceReason.BELOW_RELEVANCE_THRESHOLD,
                best_score=0.1,
            ),
            [],
        )

    monkeypatch.setattr(chat_service_mod, "assess_and_select_knowledge", reject_relevance)
    retrieval = ExactRecallRetrieval(cited_chunk)
    llm = ScriptedResolutionLLM({}, answer="Refunds may be requested within 30 days. [1]")
    service = _service(session, conversation_repository, message_repository, llm)
    service._retrieval = retrieval
    turn = await service.send_message(
        conversation.id, MessageSendRequest(content="Make it shorter.")
    )
    assert not retrieval.exact_calls
    assert retrieval.retrieve_calls
    assert turn.assistant_message.insufficient_evidence_reason is None
    assert turn.assistant_message.metadata["knowledge_repair"]["status"] == "not_needed"
    assert "[1]" in turn.assistant_message.content


def test_new_review_citations_do_not_use_prior_coverage_origin(
    session,
    conversation_repository,
    message_repository,
):
    chunk = _reusable_chunk()
    prior = uuid.uuid4()
    llm = ScriptedResolutionLLM({}, answer="ok")
    service = _service(session, conversation_repository, message_repository, llm)
    snapshots = service._citations_for(
        [chunk],
        prompt_version="v1",
        originating_assistant_message_id=prior,
        inherited_coverage=None,
        knowledge_repair={"coverage": {"quotes_validated": True}},
    )
    assert snapshots[0]["coverage_origin_message_id"] is None
    inherited = service._citations_for(
        [chunk],
        prompt_version="v1",
        originating_assistant_message_id=prior,
        inherited_coverage={
            "coverage_origin_message_id": str(prior),
            "coverage_status": "complete",
        },
        knowledge_repair={"status": "not_needed"},
    )
    assert inherited[0]["coverage_origin_message_id"] == str(prior)


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


async def test_simple_factual_turn_uses_only_answer_generation(
    session,
    conversation_repository,
    message_repository,
    conversation,
):
    message_repository.list_recent_for_conversation.return_value = []
    llm = ScriptedResolutionLLM({}, answer="Refunds are available within 30 days. [1]")
    service = _service(session, conversation_repository, message_repository, llm)
    service._evidence_approach = "factual"
    turn = await service.send_message(
        conversation.id, MessageSendRequest(content="What is the current refund period?")
    )
    assert llm.generate_calls == 1 and llm.resolver_calls == 0
    assert turn.assistant_message.insufficient_evidence_reason is None
    assert turn.assistant_message.metadata["knowledge_repair"]["status"] == "not_needed"
    assert turn.assistant_message.metadata["evidence_summary"]["coverage"] == "not_assessed"


async def test_first_historical_turn_can_clarify_cutoff_without_searching_current_sources(
    session,
    conversation_repository,
    message_repository,
    conversation,
):
    message_repository.list_recent_for_conversation.return_value = []
    llm = ScriptedResolutionLLM(
        _resolved_payload(
            relation="standalone",
            outcome="clarify",
            effective_question="What rules applied in 2024?",
            clarification_question="Which date in 2024 should I use?",
        )
    )
    service = _service(session, conversation_repository, message_repository, llm)
    retrieval = CapturingRetrieval()
    service._retrieval = retrieval
    turn = await service.send_message(
        conversation.id, MessageSendRequest(content="What rules applied in historical 2024?")
    )
    assert llm.resolver_calls == 1 and llm.generate_calls == 1
    assert not retrieval.calls
    assert turn.assistant_message.content == "Which date in 2024 should I use?"


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
            bindings=[
                {
                    "kind": "scenario_parameter",
                    "active_value": "75,000",
                    "origin": "user_literal",
                    "references": [
                        {
                            "message_id": str(prior_user.id),
                            "role": "user",
                            "excerpt": "75,000",
                        }
                    ],
                }
            ],
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
    assert next(item for item in events if isinstance(item, str)) == "Which plan should I use?"
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
    assert recorded["failure_code"] == "malformed_json"


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


def test_preparation_progress_uses_active_phases_not_llm_counts():
    from app.modules.conversations.services.chat_service import _preparation_progress
    from app.platform.providers.request_work import RequestWork

    work = RequestWork(uuid.uuid4())
    assert _preparation_progress(work) == ("understanding_request", "Understanding request")
    with work.stage("finding_cited_passages"):
        assert _preparation_progress(work) == (
            "finding_cited_passages",
            "Finding cited passages",
        )
    with work.stage("finding_relevant_sources"):
        assert _preparation_progress(work) == (
            "finding_relevant_sources",
            "Finding relevant sources",
        )
    with work.stage("checking_missing_details"):
        assert _preparation_progress(work) == (
            "checking_missing_details",
            "Checking missing details",
        )
    with work.stage("checking_source_applicability"):
        assert _preparation_progress(work) == (
            "checking_source_applicability",
            "Checking source applicability",
        )
    with work.stage("preparing_answer"):
        assert _preparation_progress(work) == ("generating_answer", "Preparing your answer")
    work.counts["llm_calls"] = 3
    work.counts["rerank_calls"] = 2
    assert _preparation_progress(work) == ("understanding_request", "Understanding request")
    with work.stage("finding_cited_passages"), work.stage("coverage_review"):
        assert _preparation_progress(work) == (
            "checking_missing_details",
            "Checking missing details",
        )


async def test_stream_wrapper_awaits_nested_delivery_cleanup_in_owner_task():
    from app.platform.providers.request_work import RequestWork, current_request_work

    service = object.__new__(ChatService)
    service._work = RequestWork(uuid.uuid4())
    cleanup: list[tuple[object, object]] = []

    async def delivery(*_args, **_kwargs):
        try:
            yield "token"
        finally:
            cleanup.append((asyncio.current_task(), current_request_work()))

    service._deliver_stream_message = delivery
    stream = service.stream_message(uuid.uuid4(), None)
    assert await anext(stream) == "token"
    owner = asyncio.current_task()
    await stream.aclose()

    assert cleanup == [(owner, service._work)]


@pytest.mark.parametrize(
    ("question", "calculation"),
    [
        ("What annual compliance obligations does a non-operating private company have?", False),
        ("ব্যবসা শুরু না করা প্রাইভেট কোম্পানির বার্ষিক করণীয় কী?", False),
        ("Calculate my income tax.", True),
        ("আমার আয়কর হিসাব করুন।", True),
    ],
)
def test_authority_refusal_describes_compliance_without_calling_it_calculation(
    question: str, calculation: bool
) -> None:
    from app.modules.conversations.schemas.message import InsufficientEvidenceReason

    service = MagicMock()
    service._evidence_approach = "authoritative"
    prepared = MagicMock()
    prepared.web_search_diagnostics = {}
    prepared.evidence.reason = InsufficientEvidenceReason.UNRESOLVED_AUTHORITY
    chunk = MagicMock()
    chunk.metadata = {"source_role": "primary", "source_lifecycle_status": "active"}
    prepared.chunks = [chunk]
    content = ChatService._insufficient_content(service, prepared, question)
    mentions_calculation = "final calculation" in content or "চূড়ান্ত হিসাব" in content
    assert mentions_calculation is calculation
    assert "amendment" in content or "সংশোধন" in content


@pytest.mark.parametrize("question", ["What filings are required?", "কী দাখিল করতে হবে?"])
@pytest.mark.parametrize("status", ["repair_unavailable", "incomplete_plan"])
def test_invalid_coverage_response_is_not_reported_as_missing_law(
    question: str, status: str
) -> None:
    from app.modules.conversations.schemas.message import InsufficientEvidenceReason

    service = MagicMock()
    service._evidence_approach = "authoritative"
    prepared = MagicMock()
    prepared.web_search_diagnostics = {}
    prepared.evidence.reason = InsufficientEvidenceReason.UNRESOLVED_AUTHORITY
    prepared.retrieval_diagnostics = {
        "knowledge_repair": {
            "status": status,
            "failure_reason": "invalid_model_response",
        }
    }
    content = ChatService._insufficient_content(service, prepared, question)
    assert "verification failure" in content or "যাচাই প্রক্রিয়ার ত্রুটি" in content
    assert "amendment evidence" not in content and "সংশোধনের নির্দিষ্ট প্রমাণ" not in content


async def test_rewrite_inherits_document_and_metadata_scope(
    session,
    conversation_repository,
    message_repository,
    conversation,
):
    cited_chunk = _reusable_chunk()
    prior_user, prior_assistant = _history_messages(
        conversation,
        user_content="What is the refund period?",
        assistant_content="A request may be made within 30 days of purchase. [1]",
    )
    prior_assistant.citations = [
        _reusable_citation(
            cited_chunk,
            evidence_scope_document_id=str(cited_chunk.document_id),
            evidence_scope_metadata_filter={"country": "BD"},
        )
    ]
    message_repository.list_recent_for_conversation.return_value = [prior_user, prior_assistant]
    retrieval = ExactRecallRetrieval(cited_chunk)
    llm = ScriptedResolutionLLM({}, answer="Refunds are available within 30 days. [1]")
    service = _service(
        session,
        conversation_repository,
        message_repository,
        llm,
        chat_config=ChatConfig(
            system_prompt_version="v1",
            response_mode=ResponseMode.INDEXED_THEN_WEB,
        ),
    )
    service._retrieval = retrieval
    service._web_search = FakeWebSearch()
    turn = await service.send_message(
        conversation.id, MessageSendRequest(content="Make it shorter.")
    )
    assert retrieval.exact_calls[0]["document_id"] == cited_chunk.document_id
    assert retrieval.exact_calls[0]["metadata_filter"] == {"country": "BD"}
    assert not service._web_search.calls
    assert turn.assistant_message.metadata["web_search"]["status"] in {
        "not_requested",
        "suppressed_scoped_request",
    }
    assert turn.assistant_message.metadata["presentation_reuse"]["status"] == "reused"


async def test_rewrite_carries_validated_partial_scope_into_generation_and_policy(
    session,
    conversation_repository,
    message_repository,
    conversation,
):
    cited_chunk = _reusable_chunk()
    prior_user, prior_assistant = _history_messages(
        conversation,
        user_content="What AGM and filing rules apply?",
        assistant_content="Private companies must hold an AGM. [1]",
    )
    prior_assistant.citations = [_reusable_citation(cited_chunk)]
    prior_assistant.message_metadata = {
        "knowledge_repair": {
            "status": "partial_answer",
            "partial_answer": {
                "scope": "AGM duty",
                "requirement_ids": ["R1"],
                "exclusions": ["filing deadline"],
                "pending": ["filing deadline"],
            },
            "missing_inputs": ["company type"],
            "coverage": {"quotes_validated": True, "partial_scope_validated": True},
        },
        "evidence_summary": {
            "coverage": "partial",
            "coverage_method": "validated_requirements",
        },
    }
    message_repository.list_recent_for_conversation.return_value = [prior_user, prior_assistant]
    retrieval = ExactRecallRetrieval(cited_chunk)
    llm = ScriptedResolutionLLM({}, answer="Private companies must hold an AGM. [1]")
    service = _service(session, conversation_repository, message_repository, llm)
    service._retrieval = retrieval
    turn = await service.send_message(
        conversation.id, MessageSendRequest(content="Make it shorter.")
    )
    assert turn.assistant_message.metadata["presentation_reuse"]["status"] == "reused"
    policy = turn.assistant_message.metadata["response_policy"]["answerable_scope"]
    assert policy["partial"] is True
    assert policy["complete"] is False
    assert "filing deadline" in policy["unresolved_facets"]
    assert "company type" in policy["missing_inputs"]
    assert turn.assistant_message.metadata["inherited_coverage"][
        "coverage_origin_message_id"
    ] == str(prior_assistant.id)
    assert turn.assistant_message.metadata["turn_resolution"]["retained_factual_question"] == (
        "What AGM and filing rules apply?"
    )
    prompt = "\n".join(message.content for message in llm.generation_prompts[0])
    assert "filing deadline" in prompt
    assert "company type" in prompt

    rewrite_user, rewrite_assistant = _history_messages(
        conversation,
        user_content="Make it shorter.",
        assistant_content="Private companies must hold an AGM. [1]",
    )
    saved = message_repository.add.call_args_list[-1].args[0]
    rewrite_assistant.citations = list(saved.citations or [])
    rewrite_assistant.message_metadata = dict(saved.message_metadata or {})
    message_repository.list_recent_for_conversation.return_value = [
        prior_user,
        prior_assistant,
        rewrite_user,
        rewrite_assistant,
    ]
    second = await service.send_message(
        conversation.id, MessageSendRequest(content="Translate the previous answer.")
    )
    assert second.assistant_message.metadata["presentation_reuse"]["status"] == "reused"
    assert second.assistant_message.metadata["inherited_coverage"][
        "coverage_origin_message_id"
    ] == str(prior_assistant.id)
    assert (
        second.assistant_message.metadata["response_policy"]["answerable_scope"]["partial"] is True
    )
    assert second.assistant_message.metadata["turn_resolution"]["retained_factual_question"] == (
        "What AGM and filing rules apply?"
    )


async def test_invalidated_rewrite_revalidates_and_preserves_partial_scope(
    session,
    conversation_repository,
    message_repository,
    conversation,
):
    cited_chunk = _reusable_chunk("Private companies must hold an AGM.")
    prior_user, prior_assistant = _history_messages(
        conversation,
        user_content="What AGM and filing rules apply?",
        assistant_content="Private companies must hold an AGM. [1]",
    )
    prior_assistant.citations = [_reusable_citation(cited_chunk)]
    prior_assistant.message_metadata = {
        "knowledge_repair": {
            "status": "partial_answer",
            "partial_answer": {
                "scope": "AGM duty",
                "requirement_ids": ["R1"],
                "exclusions": ["filing deadline"],
            },
            "missing_inputs": ["company type"],
            "coverage": {"quotes_validated": True, "partial_scope_validated": True},
        },
        "evidence_summary": {"coverage": "partial"},
    }
    message_repository.list_recent_for_conversation.return_value = [prior_user, prior_assistant]

    class RepairableRecall(ExactRecallRetrieval):
        async def retrieve(self, **kwargs: object) -> ContextRetrievalResult:
            result = await super().retrieve(**kwargs)
            return ContextRetrievalResult(
                chunks=result.chunks,
                diagnostics={
                    **self._identity_diagnostics(kwargs, len(result.chunks)),
                    "index_build_id": cited_chunk.metadata["index_build_id"],
                    "source_metadata_generation": cited_chunk.metadata[
                        "source_metadata_generation"
                    ],
                },
            )

    class ScopeReviewLLM(ScriptedResolutionLLM):
        review_stage = 0
        review_payload: dict[str, object] | None = None

        async def generate(self, messages, *, temperature, max_tokens):
            if self.review_stage == 0:
                self.review_stage = 1
                self.generate_calls += 1
                self.review_payload = json.loads(messages[-1].content)
                plan = {
                    "queries": [{"query": "filing deadline", "requirement_ids": ["R2"]}],
                    "requirements": [
                        {"requirement_id": "R1", "description": "AGM duty"},
                        {"requirement_id": "R2", "description": "filing deadline"},
                    ],
                    "coverage": {
                        "complete": False,
                        "missing": ["filing deadline"],
                        "checks": [
                            {
                                "requirement_id": "R1",
                                "description": "AGM duty",
                                "supported": True,
                                "evidence": [{"chunk_id": "E1", "quote": cited_chunk.content}],
                            },
                            {
                                "requirement_id": "R2",
                                "description": "filing deadline",
                                "supported": False,
                                "evidence": [],
                            },
                        ],
                        "partial_answer": {
                            "scope": "AGM duty",
                            "requirement_ids": ["R1"],
                            "exclusions": ["filing deadline"],
                        },
                    },
                }
                return ChatCompletionResult(
                    content=json.dumps(plan),
                    provider="echo",
                    model="test",
                    finish_reason="stop",
                    usage=ChatUsage(3, 5),
                    provider_version="1",
                )
            if self.review_stage == 1:
                self.review_stage = 2
                self.generate_calls += 1
                delta = {
                    "complete": False,
                    "missing": ["filing deadline"],
                    "checks": [
                        {
                            "requirement_id": "R2",
                            "description": "filing deadline",
                            "supported": False,
                            "evidence": [],
                        }
                    ],
                    "partial_answer": {
                        "scope": "AGM duty",
                        "requirement_ids": ["R1"],
                        "exclusions": ["filing deadline"],
                    },
                }
                return ChatCompletionResult(
                    content=json.dumps(delta),
                    provider="echo",
                    model="test",
                    finish_reason="stop",
                    usage=ChatUsage(3, 5),
                    provider_version="1",
                )
            return await super().generate(messages, temperature=temperature, max_tokens=max_tokens)

    retrieval = RepairableRecall(cited_chunk, configuration_hash="z" * 64)
    llm = ScopeReviewLLM({}, answer="Private companies must hold an AGM. [1]")
    service = _service(session, conversation_repository, message_repository, llm)
    service._retrieval = retrieval

    turn = await service.send_message(
        conversation.id, MessageSendRequest(content="Make it shorter.")
    )

    metadata = turn.assistant_message.metadata
    assert metadata["presentation_reuse"]["status"] == "fallback"
    assert metadata["presentation_reuse"]["new_coverage_review"] is True
    assert llm.review_payload is not None
    assert "prior_validated_scope_constraints" in llm.review_payload
    assert metadata["response_policy"]["answerable_scope"]["partial"] is True
    assert "filing deadline" in metadata["response_policy"]["answerable_scope"]["unresolved_facets"]
    assert "company type" in metadata["response_policy"]["answerable_scope"]["missing_inputs"]


async def test_search_adapter_exact_recall_rejects_changed_modifier_scope(
    session,
    conversation_repository,
    message_repository,
    conversation,
):
    from app.core.config import RetrievalStrategy
    from app.dependencies.conversations import SearchServiceRetrievalAdapter
    from app.modules.retrieval.schemas.search import (
        RetrievalResult,
        SearchDiagnostics,
        SearchRequest,
        SearchResponse,
    )

    cited_chunk = _reusable_chunk()
    relationship_id = uuid.uuid4()
    base_revision = uuid.uuid4()
    modifier_revision = uuid.uuid4()
    cited_chunk = replace(
        cited_chunk,
        metadata={
            **cited_chunk.metadata,
            "source_revision_id": str(base_revision),
        },
    )
    saved = {
        "relationship_id": str(relationship_id),
        "base_revision_id": str(base_revision),
        "modifier_revision_id": str(modifier_revision),
        "target_provisions": ["Section 21 — Rebate"],
        "modifier_effective_from": "2020-01-01",
        "base_effective_from": "2018-01-01",
    }
    current = {
        **saved,
        "outcome": "already_in_recall",
        "target_provisions": ["Section 22 — Limit"],
    }
    prior_user, prior_assistant = _history_messages(
        conversation,
        user_content="What is the rebate?",
        assistant_content="The rebate is 15%. [1]",
    )
    prior_assistant.citations = [
        _reusable_citation(cited_chunk, relationship_recall_provenance=[saved])
    ]
    message_repository.list_recent_for_conversation.return_value = [prior_user, prior_assistant]

    class FakeSearch:
        def __init__(self) -> None:
            self.exact_calls: list[dict[str, object]] = []
            self.search_calls: list[object] = []

        async def recall_indexed_identities(self, **kwargs: object) -> SearchResponse:
            self.exact_calls.append(kwargs)
            return SearchResponse(
                results=[
                    RetrievalResult(
                        chunk_id=cited_chunk.chunk_id,
                        document_id=cited_chunk.document_id,
                        chunk_index=cited_chunk.chunk_index,
                        content=cited_chunk.content,
                        score=1.0,
                        filename=cited_chunk.filename,
                        metadata=cited_chunk.metadata,
                    )
                ],
                query=str(kwargs.get("query") or ""),
                top_k=1,
                diagnostics=SearchDiagnostics(
                    strategy=RetrievalStrategy.HYBRID,
                    duration_ms=1,
                    rerank_requested=False,
                    rerank_status="skipped",
                    configuration_hash=cited_chunk.metadata.get("configuration_hash"),
                    modifies_expansion_records=[current],
                ),
            )

        async def search(
            self,
            request: SearchRequest,
            *,
            adjacent_to: object = None,
            cited_chunk_ids: object = None,
        ) -> SearchResponse:
            del adjacent_to, cited_chunk_ids
            self.search_calls.append(request)
            return SearchResponse(
                results=[
                    RetrievalResult(
                        chunk_id=cited_chunk.chunk_id,
                        document_id=cited_chunk.document_id,
                        chunk_index=cited_chunk.chunk_index,
                        content=cited_chunk.content,
                        score=1.0,
                        filename=cited_chunk.filename,
                        metadata=cited_chunk.metadata,
                    )
                ],
                query=request.query,
                top_k=request.top_k or 1,
                diagnostics=SearchDiagnostics(
                    strategy=RetrievalStrategy.HYBRID,
                    duration_ms=1,
                    rerank_requested=False,
                    rerank_status="skipped",
                    configuration_hash=cited_chunk.metadata.get("configuration_hash"),
                ),
            )

    fake = FakeSearch()
    llm = ScriptedResolutionLLM({}, answer="The rebate is 15%. [1]")
    service = _service(session, conversation_repository, message_repository, llm)
    service._retrieval = SearchServiceRetrievalAdapter(fake)  # type: ignore[arg-type]
    turn = await service.send_message(
        conversation.id, MessageSendRequest(content="Make it shorter.")
    )
    assert fake.exact_calls
    assert fake.search_calls
    assert turn.assistant_message.metadata["presentation_reuse"]["reuse_failure_category"] == (
        "authority_dependency_changed"
    )


def _governed_base_and_modifier() -> tuple[ContextChunk, ContextChunk, dict[str, object]]:
    base_revision = uuid.uuid4()
    modifier_revision = uuid.uuid4()
    relationship_id = uuid.uuid4()
    question = "What is the rebate limit?"
    original = QueryVariant(
        variant_id="original",
        kind=QueryVariantKind.ORIGINAL,
        language="en",
        text=question,
    )
    base_content = (
        "Section 20 — Eligible Investment\nApproved savings certificates.\n\n"
        "Section 21 — Investment Rebate Rate\nThe rebate is 15%.\n\n"
        "Section 22 — Rebate Limit\nThe rebate cannot exceed tax liability."
    )
    modifier_content = "Section 21 — Investment Rebate Rate\nThe rebate is 10%."
    config_hash = "a" * 64

    def governed_chunk(
        *,
        content: str,
        score: float,
        related: bool,
        revision: uuid.UUID,
    ) -> ContextChunk:
        raw_hash = content_hash(content)
        return ContextChunk(
            chunk_id=uuid.uuid4(),
            document_id=uuid.uuid4(),
            chunk_index=0,
            content=content,
            score=score,
            filename="amendment.txt" if related else "act.txt",
            chunk_hash=raw_hash,
            semantic_score=0.1,
            rerank_relevance_score=score,
            evidence_relevance_score=score,
            evidence_score_method="reranker_relevance",
            evidence_calibration_id=RERANKER_RELEVANCE_CALIBRATION_ID,
            query_variants=(original,),
            metadata={
                "indexed_chunk_hash": raw_hash,
                "configuration_hash": config_hash,
                "rerank_status": "applied",
                "retrieval_scope": "related_modifier" if related else "direct",
                "source_revision_id": str(revision),
            },
        )

    base = governed_chunk(content=base_content, score=0.94, related=False, revision=base_revision)
    modifier = governed_chunk(
        content=modifier_content, score=0.82, related=True, revision=modifier_revision
    )
    record = {
        "relationship_id": str(relationship_id),
        "relationship_type": "modifies",
        "outcome": "expanded",
        "base_revision_id": str(base_revision),
        "modifier_revision_id": str(modifier_revision),
        "target_provisions": ["Section 21 — Investment Rebate Rate"],
        "modifier_effective_from": "2020-01-01",
    }
    return base, modifier, record


class GovernedRecallRetrieval:
    supports_cited_retrieval = True
    supports_exact_recall = True

    def __init__(
        self,
        base: ContextChunk,
        modifier: ContextChunk,
        record: dict[str, object],
    ) -> None:
        self.base = base
        self.modifier = modifier
        self.record = record
        self.omit_modifier = False
        self.retrieve_calls: list[dict[str, object]] = []
        self.exact_calls: list[dict[str, object]] = []

    async def retrieve(self, **kwargs: object) -> ContextRetrievalResult:
        self.retrieve_calls.append(kwargs)
        return ContextRetrievalResult(
            chunks=[self.base, self.modifier],
            diagnostics={
                "rerank_status": "applied",
                "modifies_expansion_status": "expanded",
                "modifies_expansion_records": [self.record],
                "configuration_hash": self.base.metadata["configuration_hash"],
                "retrieved_candidate_count": 2,
            },
        )

    async def retrieve_exact(self, **kwargs: object) -> ContextRetrievalResult:
        self.exact_calls.append(kwargs)
        requested = kwargs.get("chunk_ids") or []
        available = [self.base] if self.omit_modifier else [self.base, self.modifier]
        by_id = {chunk.chunk_id: chunk for chunk in available}
        chunks = (
            [by_id[item] for item in requested if item in by_id]
            if isinstance(requested, list) and requested
            else available
        )
        return ContextRetrievalResult(
            chunks=[_identity_recalled_chunk(item) for item in chunks],
            diagnostics={
                "rerank_status": "skipped",
                "configuration_hash": self.base.metadata["configuration_hash"],
                "modifies_expansion_records": [self.record],
                "modifies_expansion_status": "observe",
            },
        )


async def test_governed_presentation_rewrite_reuses_real_authority_snapshots(
    session,
    conversation_repository,
    message_repository,
    conversation,
):
    base, modifier, record = _governed_base_and_modifier()
    retrieval = GovernedRecallRetrieval(base, modifier, record)
    llm = CitedLLM("The rebate cannot exceed tax liability. [1]")
    service = _service(session, conversation_repository, message_repository, llm)
    service._retrieval = retrieval
    first = await service.send_message(
        conversation.id, MessageSendRequest(content="What is the rebate limit?")
    )
    saved = message_repository.add.call_args_list[-1].args[0]
    citations = list(saved.citations or [])
    assert citations
    assert citations[0]["chunk_id"] == str(base.chunk_id)
    assert citations[0]["authority_dependencies"]
    assert citations[0]["authority_dependencies"][0]["modifier_recalled"] is True
    assert citations[0]["authority_dependencies"][0]["modifier_chunk_id"] == str(modifier.chunk_id)

    first_user = next(
        call.args[0]
        for call in message_repository.add.call_args_list
        if call.args[0].role is MessageRole.USER
    )
    message_repository.list_recent_for_conversation.return_value = [first_user, saved]
    rewrite = await service.send_message(
        conversation.id, MessageSendRequest(content="Make it shorter.")
    )
    assert retrieval.exact_calls
    assert rewrite.assistant_message.metadata["presentation_reuse"]["status"] == "reused"
    assert first.assistant_message.citations[0].chunk_id == base.chunk_id

    retrieval.omit_modifier = True
    missing = await service.send_message(
        conversation.id, MessageSendRequest(content="Translate the previous answer.")
    )
    assert missing.assistant_message.metadata["presentation_reuse"]["status"] == "fallback"
    assert missing.assistant_message.metadata["presentation_reuse"]["reuse_failure_category"] == (
        "missing_identity"
    )


async def test_governed_presentation_rewrite_reuses_cited_modifier_passage(
    session,
    conversation_repository,
    message_repository,
    conversation,
):
    from app.modules.conversations.current_authority import remove_superseded_provisions

    base, modifier, record = _governed_base_and_modifier()
    redacted_base = next(
        chunk
        for chunk in remove_superseded_provisions([base, modifier], [record])
        if chunk.chunk_id == base.chunk_id
    )
    dependency = {
        **record,
        "modifier_recalled": True,
        "modifier_chunk_id": str(modifier.chunk_id),
    }
    prior_user, prior_assistant = _history_messages(
        conversation,
        user_content="What is the current rebate rate?",
        assistant_content="The rebate is 10%. [2]",
    )
    prior_assistant.citations = [
        _reusable_citation(
            redacted_base,
            evidence_source_chunk_hash=content_hash(redacted_base.content),
            evidence_span_hash=content_hash(redacted_base.content),
            evidence_chunk_char_end=len(redacted_base.content),
            source_revision_id=base.metadata["source_revision_id"],
            authority_dependencies=[dependency],
        ),
        _reusable_citation(
            modifier,
            source_revision_id=modifier.metadata["source_revision_id"],
            authority_dependencies=[dependency],
        ),
    ]
    message_repository.list_recent_for_conversation.return_value = [prior_user, prior_assistant]
    retrieval = GovernedRecallRetrieval(base, modifier, record)
    llm = ScriptedResolutionLLM({}, answer="The rebate is 10%. [2]")
    service = _service(session, conversation_repository, message_repository, llm)
    service._retrieval = retrieval
    rewrite = await service.send_message(
        conversation.id, MessageSendRequest(content="Make it shorter.")
    )
    assert retrieval.exact_calls
    requested = retrieval.exact_calls[0]["chunk_ids"]
    assert modifier.chunk_id in requested
    assert rewrite.assistant_message.metadata["presentation_reuse"]["status"] == "reused"
    assert retrieval.retrieve_calls == []


class _ExpireAfterRollback:
    """Stand-in that fails if ChatService lazy-loads history after rollback."""

    def __init__(self, inner: Message) -> None:
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "expired", False)

    def __getattr__(self, name: str) -> object:
        if object.__getattribute__(self, "expired"):
            raise RuntimeError("greenlet_spawn has not been called; can't call await_only() here")
        return getattr(object.__getattribute__(self, "_inner"), name)

    def __setattr__(self, name: str, value: object) -> None:
        if name in {"expired", "_inner"}:
            object.__setattr__(self, name, value)
            return
        setattr(object.__getattribute__(self, "_inner"), name, value)


async def test_presentation_rewrite_does_not_lazy_load_history_after_rollback(
    session,
    conversation_repository,
    message_repository,
    conversation,
):
    base, modifier, record = _governed_base_and_modifier()
    dependency = {
        **record,
        "modifier_recalled": True,
        "modifier_chunk_id": str(modifier.chunk_id),
    }
    prior_user, prior_assistant = _history_messages(
        conversation,
        user_content="What is the current rebate rate?",
        assistant_content="The rebate is 10%. [2]",
    )
    prior_assistant.citations = [
        _reusable_citation(
            base,
            source_revision_id=base.metadata["source_revision_id"],
            authority_dependencies=[dependency],
        ),
        _reusable_citation(
            modifier,
            source_revision_id=modifier.metadata["source_revision_id"],
            authority_dependencies=[dependency],
        ),
    ]
    prior_assistant.message_metadata = {
        "knowledge_repair": {"status": "not_needed"},
        "evidence_summary": {"coverage": "complete"},
    }
    wrapped_user = _ExpireAfterRollback(prior_user)
    wrapped_assistant = _ExpireAfterRollback(prior_assistant)
    message_repository.list_recent_for_conversation.return_value = [
        wrapped_user,
        wrapped_assistant,
    ]

    async def expire_history(*_args: object, **_kwargs: object) -> None:
        wrapped_user.expired = True
        wrapped_assistant.expired = True

    session.rollback.side_effect = expire_history
    retrieval = GovernedRecallRetrieval(base, modifier, record)
    llm = ScriptedResolutionLLM({}, answer="The rebate is 10%. [2]")
    service = _service(session, conversation_repository, message_repository, llm)
    service._retrieval = retrieval
    rewrite = await service.send_message(
        conversation.id, MessageSendRequest(content="Make it shorter.")
    )
    assert retrieval.exact_calls
    assert rewrite.assistant_message.metadata["presentation_reuse"]["status"] == "reused"


async def test_scoped_rewrite_keeps_effective_modifier_excluded_notice(
    session,
    conversation_repository,
    message_repository,
    conversation,
):
    base_revision = uuid.uuid4()
    cited_chunk = _reusable_chunk()
    cited_chunk = replace(
        cited_chunk,
        metadata={
            **cited_chunk.metadata,
            "source_revision_id": str(base_revision),
        },
    )
    expansion = {
        "relationship_id": str(uuid.uuid4()),
        "relationship_type": "modifies",
        "outcome": "expanded",
        "base_revision_id": str(base_revision),
        "modifier_revision_id": str(uuid.uuid4()),
        "target_provisions": ["Section 21 — Investment Rebate Rate"],
        "modifier_effective_from": "2020-01-01",
        "modifier_recalled": False,
    }
    prior_user, prior_assistant = _history_messages(
        conversation,
        user_content="What is the refund period?",
        assistant_content="A request may be made within 30 days of purchase. [1]",
    )
    prior_assistant.citations = [
        _reusable_citation(
            cited_chunk,
            evidence_scope_document_id=str(cited_chunk.document_id),
            authority_dependencies=[expansion],
            source_revision_id=str(base_revision),
        )
    ]
    message_repository.list_recent_for_conversation.return_value = [prior_user, prior_assistant]
    retrieval = ExactRecallRetrieval(cited_chunk, expansion_records=[expansion])
    llm = ScriptedResolutionLLM({}, answer="Refunds are available within 30 days. [1]")
    service = _service(session, conversation_repository, message_repository, llm)
    service._retrieval = retrieval
    turn = await service.send_message(
        conversation.id, MessageSendRequest(content="Make it shorter.")
    )
    assert retrieval.exact_calls[0]["document_id"] == cited_chunk.document_id
    assert turn.assistant_message.metadata["scope_current_authority"]["status"] == (
        "effective_modifier_excluded_by_scope"
    )
    assert turn.assistant_message.metadata["presentation_reuse"]["status"] == "reused"
