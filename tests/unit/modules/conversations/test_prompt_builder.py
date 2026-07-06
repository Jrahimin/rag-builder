"""Unit tests for PromptBuilder."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.models.message import Message, MessageRole
from app.modules.conversations.ports import ContextChunk
from app.modules.conversations.prompt_builder import PromptBuilder
from app.modules.conversations.prompts.registry import require_prompt_template
from app.platform.providers.contracts.llm import ChatRole

pytestmark = pytest.mark.unit


def test_build_includes_system_context_and_user_question() -> None:
    template = require_prompt_template("v1")
    chunk = ContextChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=0,
        content="policy text",
        score=0.9,
        filename="policy.txt",
        chunk_hash="abc",
    )
    history = [
        Message(
            id=uuid.uuid4(),
            project_id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            role=MessageRole.USER,
            content="earlier question",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
    ]
    messages = PromptBuilder().build(
        template=template,
        context_chunks=[chunk],
        history=history,
        user_question="What is the policy?",
    )
    assert messages[0].role is ChatRole.SYSTEM
    assert "policy text" in messages[0].content
    assert messages[-1].role is ChatRole.USER
    assert messages[-1].content == "What is the policy?"
