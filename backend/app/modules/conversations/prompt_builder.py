"""Assemble provider-ready chat messages from context and history."""

from __future__ import annotations

from app.models.message import Message, MessageRole
from app.modules.conversations.ports import ContextChunk
from app.modules.conversations.prompts.registry import PromptTemplate
from app.platform.providers.contracts.llm import ChatMessage, ChatRole


class PromptBuilder:
    """Format prompts only — no retrieval or budget decisions."""

    def build(
        self,
        *,
        template: PromptTemplate,
        context_chunks: list[ContextChunk],
        history: list[Message],
        user_question: str,
    ) -> list[ChatMessage]:
        context_block = self._format_context(context_chunks)
        system_content = template.template
        if context_block:
            system_content = f"{template.template}\n\nContext:\n{context_block}"

        messages: list[ChatMessage] = [ChatMessage(role=ChatRole.SYSTEM, content=system_content)]

        for message in history:
            if message.role is MessageRole.SYSTEM:
                continue
            role = ChatRole.USER if message.role is MessageRole.USER else ChatRole.ASSISTANT
            messages.append(ChatMessage(role=role, content=message.content))

        messages.append(ChatMessage(role=ChatRole.USER, content=user_question))
        return messages

    def _format_context(self, chunks: list[ContextChunk]) -> str:
        if not chunks:
            return ""
        lines: list[str] = []
        for index, chunk in enumerate(chunks, start=1):
            header = f"[{index}] source={chunk.filename}"
            if chunk.page_number is not None:
                header = f"{header} page={chunk.page_number}"
            lines.append(f"{header}\n{chunk.content}")
        return "\n\n".join(lines)
