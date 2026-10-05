"""Endpoint/model capabilities, effective call provenance and verified rendering."""

from __future__ import annotations

import json
import uuid

import pytest

from app.modules.conversations.answer_draft import AnswerDraft, render_verified_segments
from app.modules.conversations.notices import draft_scope_notice
from app.platform.providers.capabilities import endpoint_identity, structured_output_capability
from app.platform.providers.contracts.llm import (
    ChatCompletionResult,
    ChatMessage,
    ChatRole,
    ChatUsage,
)
from app.platform.providers.implementations.openai_compatible_chat import (
    OpenAICompatibleChatProvider,
)
from app.platform.providers.request_work import ObservedLLM, RequestWork

pytestmark = pytest.mark.unit


def declaration(provider="openai_compatible", model="fixture", endpoint="https://fixture.test"):
    return {
        "provider": provider,
        "model": model,
        "endpoint_hash": endpoint_identity(endpoint),
        "schema_mode": "json_schema",
        "reviewer": "offline-fixture",
        "evidence_hash": "a" * 64,
        "capability_revision": "fixture.v1",
    }


def test_schema_requires_exact_endpoint_model_attestation_and_keeps_local_validation():
    declarations = [declaration()]
    assert (
        structured_output_capability(
            "openai_compatible", "fixture", "https://fixture.test", declarations
        )["schema_mode"]
        == "json_schema"
    )
    for provider, model, endpoint in [
        ("openai", "fixture", "https://fixture.test"),
        ("openai_compatible", "other", "https://fixture.test"),
        ("openai_compatible", "fixture", "https://other.test"),
    ]:
        assert (
            structured_output_capability(provider, model, endpoint, declarations)["schema_mode"]
            == "prompt"
        )
    assert (
        structured_output_capability("openai", "gpt-6-luna", "https://api.openai.com")[
            "schema_mode"
        ]
        == "json_object"
    )
    provider = OpenAICompatibleChatProvider(
        provider_name="openai_compatible",
        api_key="not-used",
        base_url="https://fixture.test",
        model="fixture",
        provider_version="fixture",
        request_timeout_seconds=1,
        schema_capabilities=declarations,
    )
    body = provider._body(
        [ChatMessage(ChatRole.USER, "fixture")],
        max_tokens=64,
        output_contract=AnswerDraft.contract(),
        stream=False,
    )
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["response_format"]["json_schema"]["schema"] == AnswerDraft.contract().schema
    with pytest.raises(ValueError):
        AnswerDraft.model_validate_json(json.dumps({"segments": [{"text": "unbound"}]}))
    assert endpoint_identity(
        "https://user:secret@fixture.test/path?key=secret"
    ) == endpoint_identity("https://fixture.test/path")


async def test_observed_stage_retains_actual_model_reasoning_and_schema_without_provider_calls():
    class Local(OpenAICompatibleChatProvider):
        async def generate(self, messages, *, temperature=None, max_tokens, output_contract=None):
            body = self._body(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                output_contract=output_contract,
                stream=False,
            )
            assert (
                body["reasoning_effort"] == "low"
                and body["response_format"]["type"] == "json_object"
            )
            return ChatCompletionResult(
                "{}",
                self.provider_name,
                self.model_name,
                "stop",
                ChatUsage(1, 1),
                "fixture",
                self.request_provenance(output_contract, max_tokens),
            )

    provider = Local(
        provider_name="openai",
        api_key="not-used",
        base_url="https://api.openai.com",
        model="gpt-6-luna",
        provider_version="fixture",
        request_timeout_seconds=1,
    )
    work = RequestWork(uuid.uuid4())
    with work.stage("verify", purpose="claim_verification"):
        await ObservedLLM(provider, work).generate(
            [ChatMessage(ChatRole.USER, "fixture")],
            max_tokens=64,
            output_contract=AnswerDraft.contract(),
        )
    call = work.snapshot()["provider_calls"][0]
    assert call["model"] == "gpt-6-luna" and call["reasoning"] == "low"
    assert call["purpose"] == "claim_verification" and call["schema_mode"] == "json_object"
    assert call["schema_name"] == "answer_draft_v1" and call["schema_hash"]
    assert call["local_validation"] == "consumer_schema_required"


def test_rendering_keeps_verified_documentary_statements_and_typed_limits():
    rows = [
        {
            "assertion_id": "A1",
            "text": "The document proposes BDT 375,000 for AY 2026-27.",
            "proof_ids": ["P1"],
        },
        {"assertion_id": "A2", "text": "It was never enacted.", "proof_ids": ["P1"]},
    ]
    rendered = render_verified_segments(rows, supported_ids={"A1"}, proof_indexes={"P1": 1})
    assert rendered == rows[0]["text"] + " [1]" and "never enacted" not in rendered
    notice = draft_scope_notice(kind="proposal_scope", language="en", proof_ids=["P1"])
    assert "legal effect" in notice.text and "never" not in notice.text
    assert render_verified_segments(rows, supported_ids=set(), proof_indexes={"P1": 1}) == ""
    with pytest.raises(ValueError):
        render_verified_segments(rows, supported_ids={"A1"}, proof_indexes={})
