# Conversation — LLM Providers

APE treats text generation as infrastructure behind `BaseLLMProvider`. This doc describes **what each backend is**; for **how to call** them uniformly, see [Provider integration](./conversation_provider_integration.md).

## Contract

Neutral DTOs in `platform/providers/contracts/llm.py`:

| DTO | Purpose |
| --- | ------- |
| `ChatMessage` | `{role, content}` input to any backend |
| `ChatCompletionResult` | Normalized non-stream response + token usage |
| `ChatCompletionChunk` | One streaming token delta |
| `BaseLLMProvider` | `generate()` + `stream()` |

## Backends

| `APE_LLM__BACKEND` | Implementation | Typical use |
| ------------------ | -------------- | ----------- |
| `echo` | `EchoLLMProvider` | Tests, local dev without API keys |
| `openai` | `OpenAIChatProvider` | OpenAI API |
| `openai_compatible` | `OpenAICompatibleChatProvider` | vLLM, LiteLLM, any `/v1/chat/completions` host |
| `ollama` | `OllamaChatProvider` | Local Ollama |
| `gemini` | `GeminiChatProvider` | Google Gemini API |

Factory: `create_llm_provider(settings)` / `get_llm_provider()` in `llm_factory.py`.

## SDK boundary

```text
ChatService → BaseLLMProvider → openai_chat / gemini_chat / … → HTTP
```

Vendor request/response shapes never escape `implementations/`.

## Related

- [Provider integration](./conversation_provider_integration.md)
- [Conversation module](../features/conversation_module.md)
- [Provider architecture](../architecture/provider-architecture.md)
