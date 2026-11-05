"""Multi-source publication and real Message history boundary regressions."""

import re
import uuid
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.composition.evaluation import GroundedEvaluationAnswerAdapter
from app.core.config import ChatConfig, Settings
from app.modules.conversations.ports import ContextRetrievalResult
from app.modules.conversations.schemas.message import MessageResponse, MessageSendRequest
from app.modules.conversations.services.message_execution_runner_service import (
    MessageExecutionRunner,
    _history_message_from_orm,
)
from app.modules.evaluation.ports import EvaluationCaseInput
from app.modules.evaluation.schemas.evaluation import EvaluationCase
from app.platform.providers.request_work import current_request_purpose
from tests.unit.modules.conversations.test_phase2_completion import execute_contract, source
from tests.unit.modules.conversations.test_review2_publication_identity import contextual_journey

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("transport", ["regular", "sse", "get"])
@pytest.mark.parametrize("representation", ["raw", "typed"])
@pytest.mark.parametrize("termination", ["complete", "peer_rejection", "length", "timeout"])
@pytest.mark.parametrize("leading", [1, 2])
async def test_checkpoint_preserves_live_identity_and_multisource_publication(
    transport, representation, termination, leading
):
    parts, allowed, _inputs = contextual_journey(
        representation, "heading", qualifier="Private companies only"
    )
    service, provider, _conversation, repository, _question = parts
    if termination == "peer_rejection":
        allowed.difference_update(value for value in tuple(allowed) if "15 months" in value)
    unused = [
        replace(source("Unrelated archive material."), project_id=service._project_id)
        for _ in range(leading)
    ]
    original_prepare = service._runner.prepare

    async def prepare(*args, **kwargs):
        prepared = await original_prepare(*args, **kwargs)
        return replace(prepared, selected=[*unused, *prepared.selected])

    service._runner.prepare = prepare
    original_generate = provider.generate

    async def generate(*args, **kwargs):
        result = await original_generate(*args, **kwargs)
        if current_request_purpose() == "answer_generation":
            if representation == "raw":
                result = replace(result, content=result.content.replace("[1]", f"[{leading + 1}]"))
            if termination == "length":
                result = replace(result, finish_reason="length")
        return result

    provider.generate = generate
    if termination == "length" and transport == "sse":
        original_provider_stream = provider.stream

        async def length_stream(*args, **kwargs):
            async for event in original_provider_stream(*args, **kwargs):
                yield replace(event, finish_reason="length") if event.finish_reason else event

        provider.stream = length_stream
    observations = []
    original_checkpoint = service._runner._checkpoint_claims

    def checkpoint(prepared, claims):
        before = deepcopy(claims)
        original_checkpoint(prepared, claims)
        saved = service._runner._verified_checkpoint
        observations.append((before, deepcopy(claims), saved.restore(prepared.intent.scope)))

    service._runner._checkpoint_claims = checkpoint
    if termination == "timeout":
        service._chat_config = service._runner._chat_config = ChatConfig(
            execution_policy="bounded_v2", bounded_recovery_enabled=True
        )
        original_verify = service._runner._verify_claims

        async def timeout(*args, **kwargs):
            await original_verify(*args, **kwargs)
            raise TimeoutError("controlled verified checkpoint timeout")

        service._runner._verify_claims = timeout
    done = []
    if transport == "sse":
        original_stream = service.stream_message

        async def stream(*args, **kwargs):
            async for event in original_stream(*args, **kwargs):
                if isinstance(event, dict) and event.get("event") == "done":
                    done.append(event)
                yield event

        service.stream_message = stream
    answer = await execute_contract(*parts, transport)
    assert answer["grounded"] is True
    assert len(answer["claims"]) == (1 if termination == "peer_rejection" else 2)
    assert "Private companies only" in answer["content"]
    if termination != "complete":
        assert answer["terminal_outcome"]["outcome"] == "partial"
    elif representation == "typed":
        assert answer["terminal_outcome"]["outcome"] == "answered"
    if representation == "typed":
        assert all(claim["fulfilled_proof_requirements"] for claim in answer["claims"])
    if termination == "peer_rejection":
        assert "15 months" not in answer["content"]
    markers = {int(value) for value in re.findall(r"\[(\d+)\]", answer["content"])}
    assert markers == {
        item["citation_index"] for claim in answer["claims"] for item in claim["evidence"]
    }
    for claim in answer["claims"]:
        for evidence in claim["evidence"]:
            published = answer["citations"][evidence["citation_index"] - 1]
            assert published["chunk_id"] == evidence["chunk_id"] == str(provider.source.chunk_id)
            assert published["document_id"] == evidence["document_id"]
            assert published["chunk_hash"] == provider.source.chunk_hash
            assert published["excerpt"] and evidence["excerpt"] in published["excerpt"]
    assert observations
    for before, after, saved in observations:
        assert before == after, "Checkpoint capture must not mutate any live claim field"
        assert all(
            item["citation_index"] == leading + 1 for claim in after for item in claim["evidence"]
        )
        assert saved.citations[0]["chunk_id"] == str(provider.source.chunk_id)
        if representation == "typed":
            assert all(claim["fulfilled_proof_requirements"] for claim in saved.claims)
    saved = MessageResponse.from_message(repository.add.call_args.args[0]).model_dump(mode="json")
    for field in ["content", "claims", "citations"]:
        assert saved[field] == answer[field]
        if transport == "sse":
            assert done[-1][field] == answer[field]


@pytest.mark.parametrize("representation", ["snapshots_inline", "snapshots_separate", "identities"])
async def test_real_message_snapshot_history_executes_like_production_followup(
    monkeypatch, representation
):
    from app.modules.conversations import turn_resolution

    monkeypatch.setattr(
        turn_resolution, "utc_reference_datetime", lambda: datetime(2026, 10, 1, tzinfo=UTC)
    )
    parts, _allowed, _inputs = contextual_journey(
        "typed", "heading", qualifier="Private companies only"
    )
    service, provider, conversation, repository, question = parts
    controlled = replace(
        provider.source,
        metadata={**provider.source.metadata, "indexed_chunk_hash": provider.source.chunk_hash},
    )
    provider.source = controlled
    service._retrieval.retrieve.return_value = replace(
        service._retrieval.retrieve.return_value, chunks=[controlled]
    )
    initial = await service.send_message(conversation.id, MessageSendRequest(content=question))
    initial_rows = [call.args[0] for call in repository.add.call_args_list]
    snapshots = [MessageResponse.from_message(row).model_dump(mode="json") for row in initial_rows]
    history = [
        {key: item[key] for key in ["id", "role", "content", "citations"]} for item in snapshots
    ]
    assert history[-1]["citations"] and initial.assistant_message.grounded
    followup = "Rewrite that as concise bullets. Keep the same facts and citations."
    prior_citations = {}
    if representation != "snapshots_inline":
        prior_citations[snapshots[-1]["id"]] = deepcopy(history[-1]["citations"])
        history[-1]["citations"] = (
            []
            if representation == "snapshots_separate"
            else _history_message_from_orm(initial_rows[-1]).model_dump(mode="json")["citations"]
        )
    case = EvaluationCase.model_validate(
        {
            "prior_citations": prior_citations,
            "key": "real-history",
            "kind": "paraphrase",
            "query": followup,
            "relevant_chunk_ids": [str(provider.source.chunk_id)],
            "history": history,
            "prior_metadata": {snapshots[-1]["id"]: snapshots[-1]["metadata"]},
        }
    )
    repository.list_recent_for_conversation.return_value = initial_rows
    original_generate = provider.generate

    async def followup_generation(messages, **kwargs):
        if (
            current_request_purpose() == "answer_generation"
            and kwargs.get("output_contract") is None
        ):
            scopes = [
                item["answerable_scope"].split(": ", 1)[1]
                for item in provider.review()[1]["checks"]
            ]
            content = "\n\n".join(f"### Private companies only\n\n{scope} [1]" for scope in scopes)
            from app.platform.providers.contracts.llm import ChatCompletionResult, ChatUsage

            return ChatCompletionResult(
                content=content,
                provider="fixture",
                model="fixture",
                provider_version="1",
                finish_reason="stop",
                usage=ChatUsage(1, 1),
            )
        return await original_generate(messages, **kwargs)

    provider.generate = followup_generation
    service._retrieval.supports_cited_retrieval = True
    service._retrieval.supports_exact_recall = True
    service._retrieval.retrieve_exact = AsyncMock(
        return_value=ContextRetrievalResult(
            chunks=[provider.source],
            diagnostics={
                "identity_recall_status": "current",
                "relationship_discovery_status": "complete",
            },
        )
    )
    captured = []
    original_prepare = MessageExecutionRunner.prepare

    async def prepare(runner, **kwargs):
        prepared = await original_prepare(runner, **kwargs)
        captured.append((kwargs, prepared))
        return prepared

    monkeypatch.setattr(MessageExecutionRunner, "prepare", prepare)
    production = await service.send_message(conversation.id, MessageSendRequest(content=followup))
    evaluation = await GroundedEvaluationAnswerAdapter(
        settings=Settings(
            chat=service._chat_config, retrieval=service._retrieval_config, llm=service._llm_config
        ),
        llm=provider,
        project_id=conversation.project_id,
    ).answer_for_case(
        profile="semantic",
        request=MessageSendRequest(content=followup),
        hits=[],
        provenance={},
        execution_retrieval=service._retrieval,
        scenario=EvaluationCaseInput(
            query=case.query,
            top_k=5,
            history=[item.model_dump(mode="json") for item in case.history],
            prior_citations=case.prior_citations,
            prior_metadata=case.prior_metadata,
        ),
    )
    prod_inputs, prod_prepared = captured[0]
    eval_inputs, eval_prepared = captured[1]
    assert [item.model_dump(mode="json") for item in prod_inputs["resolver_source"]] == [
        item.model_dump(mode="json") for item in eval_inputs["resolver_source"]
    ]
    assert prod_inputs["citation_chunks"] == eval_inputs["citation_chunks"]
    assert prod_prepared.intent.scope == eval_prepared.intent.scope
    assert [chunk.chunk_id for chunk in prod_prepared.selected] == [
        chunk.chunk_id for chunk in eval_prepared.selected
    ]
    assert production.assistant_message.content == evaluation.answer
    assert (
        production.assistant_message.terminal_outcome.model_dump(mode="json")
        == evaluation.execution["terminal"]
    )
    assert production.assistant_message.grounded == evaluation.grounded is True
    assert prod_prepared.retrieval_diagnostics["presentation_reuse"]["status"] == "reused"
    assert eval_prepared.retrieval_diagnostics["presentation_reuse"]["status"] == "reused"
    assert service._retrieval.retrieve_exact.await_count == 2
    assert all(
        call.kwargs["chunk_ids"] == [controlled.chunk_id]
        for call in service._retrieval.retrieve_exact.await_args_list
    )
    assert all(
        citation["indexed_chunk_hash"] == controlled.chunk_hash
        for citation in eval_inputs["citation_chunks"][initial_rows[-1].id]
    )


@pytest.mark.parametrize(
    "corruption",
    [
        "foreign_message",
        "foreign_project",
        "malformed_chunk",
        "missing_identity",
        "malformed_date",
        "prior_foreign_project",
        "prior_malformed_chunk",
        "prior_disagrees",
        "identity_foreign_owner",
        "identity_malformed_date",
        "foreign_prior_owner",
        "user_prior",
    ],
)
async def test_evaluation_rejects_malformed_or_foreign_history_before_execution(
    monkeypatch, corruption
):
    parts, _allowed, _inputs = contextual_journey(
        "typed", "heading", qualifier="Private companies only"
    )
    service, provider, conversation, repository, question = parts
    await service.send_message(conversation.id, MessageSendRequest(content=question))
    rows = [call.args[0] for call in repository.add.call_args_list]
    bodies = [MessageResponse.from_message(row).model_dump(mode="json") for row in rows]
    history = [
        {key: body[key] for key in ["id", "role", "content", "citations"]} for body in bodies
    ]
    original_snapshots = deepcopy(history[-1]["citations"])
    citations = history[-1]["citations"]
    prior = {}
    if corruption == "foreign_message":
        citations[0]["message_id"] = str(uuid.uuid4())
    elif corruption == "foreign_project":
        citations[0]["project_id"] = str(uuid.uuid4())
    elif corruption == "malformed_chunk":
        citations[0]["chunk_id"] = "not-a-uuid"
    elif corruption == "missing_identity":
        del citations[0]["chunk_id"]
    elif corruption == "malformed_date":
        citations[0]["source_effective_from"] = "not-a-date"
    elif corruption in {"prior_foreign_project", "prior_malformed_chunk"}:
        history[-1]["citations"] = []
        prior[history[-1]["id"]] = original_snapshots
        if corruption == "prior_foreign_project":
            original_snapshots[0]["project_id"] = str(uuid.uuid4())
        else:
            original_snapshots[0]["chunk_id"] = "not-a-uuid"
    elif corruption == "prior_disagrees":
        prior[history[-1]["id"]] = original_snapshots
        original_snapshots[0]["chunk_id"] = str(uuid.uuid4())
    elif corruption in {"identity_foreign_owner", "identity_malformed_date"}:
        history[-1]["citations"] = _history_message_from_orm(rows[-1]).model_dump(mode="json")[
            "citations"
        ]
        prior[history[-1]["id"]] = original_snapshots
        field = "message_id" if corruption == "identity_foreign_owner" else "source_effective_from"
        history[-1]["citations"][0][field] = (
            str(uuid.uuid4()) if field == "message_id" else "not-a-date"
        )
    elif corruption == "foreign_prior_owner":
        prior[str(uuid.uuid4())] = original_snapshots
    else:
        prior[history[0]["id"]] = original_snapshots
    # The public history dictionary shape accepts these values; runtime proof
    # conversion must reject them before a provider or retrieval can run.
    if corruption != "foreign_prior_owner":
        EvaluationCase.model_validate(
            {
                "key": "bad-history",
                "kind": "no_answer",
                "query": "Rewrite that as bullets",
                "expected_no_answer": True,
                "history": history,
                "prior_citations": prior,
            }
        )
    prepared = AsyncMock()
    monkeypatch.setattr(MessageExecutionRunner, "prepare", prepared)
    before_purposes = list(provider.purposes)
    before_history = deepcopy(history)
    before_prior = deepcopy(prior)
    with pytest.raises(ValueError):
        await GroundedEvaluationAnswerAdapter(
            settings=Settings(
                chat=service._chat_config,
                retrieval=service._retrieval_config,
                llm=service._llm_config,
            ),
            llm=provider,
            project_id=conversation.project_id,
        ).answer_for_case(
            profile="semantic",
            request=MessageSendRequest(content="Rewrite that as bullets"),
            hits=[],
            provenance={},
            scenario=EvaluationCaseInput(
                query="Rewrite that as bullets", top_k=5, history=history, prior_citations=prior
            ),
        )
    prepared.assert_not_awaited()
    assert provider.purposes == before_purposes
    assert history == before_history and prior == before_prior
