"""Token accounting with an explicit conservative fallback for unknown tokenizers."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from app.platform.providers.contracts.llm import ChatMessage


@lru_cache(maxsize=32)
def _encoding(model: str) -> Any:
    try:
        import tiktoken

        return tiktoken.encoding_for_model(model)
    except (ImportError, KeyError, OSError, ValueError):
        return None


def prompt_budget(
    messages: list[ChatMessage],
    *,
    model: str,
    capacity: int,
    reserved_output: int,
    evidence_text: str = "",
) -> dict[str, Any]:
    encoding = _encoding(model)

    def count(text: str) -> int:
        if encoding is None:
            # UTF-8 bytes are a conservative bound, including Bangla. Never call it actual tokens.
            return len(text.encode("utf-8"))
        return len(encoding.encode(text, disallowed_special=()))

    text_tokens = sum(count(message.content) for message in messages)
    framing_reserve = 16 * len(messages) + 128
    prompt_tokens = text_tokens + framing_reserve
    return {
        "version": "prompt.v1",
        "model": model,
        "count_method": "model_tokenizer_with_framing_reserve"
        if encoding
        else "utf8_byte_upper_bound",
        "text_tokens": text_tokens,
        "framing_reserve": framing_reserve,
        "prompt_tokens": prompt_tokens,
        "reserved_output_tokens": reserved_output,
        "context_capacity": capacity,
        "capacity_source": "deployment_model_budget",
        "within_budget": prompt_tokens + reserved_output <= capacity,
        "instruction_and_evidence_tokens": count(messages[0].content) if messages else 0,
        "evidence_tokens": count(evidence_text),
        "instruction_tokens": count(messages[0].content.replace(evidence_text, "", 1))
        if messages and evidence_text
        else count(messages[0].content)
        if messages
        else 0,
        "history_tokens": sum(count(m.content) for m in messages[1:-1]),
        "question_tokens": count(messages[-1].content) if len(messages) > 1 else 0,
    }
