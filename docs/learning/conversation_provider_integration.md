# Conversation — Provider-Agnostic LLM Integration

How to call an LLM in APE **without** caring whether the deployment uses OpenAI, Gemini, Ollama, or a test echo backend.

> Same pattern as embeddings: **one interface, one factory, config-driven backend.**

---

## The rule

**Consumers import only:**

```text
platform/providers/contracts/llm.py     → BaseLLMProvider, ChatMessage, ChatRole, …
platform/providers/implementations/llm_factory.py → get_llm_provider(), create_llm_provider()
```

**Never import** `openai_chat`, `gemini_chat`, `ollama_chat`, etc. from module or service code.

---

## Integration pattern (all backends)

### 1. Resolve the provider (composition / DI)

```python
from app.platform.providers.implementations.llm_factory import get_llm_provider

llm = get_llm_provider()  # backend from APE_LLM__BACKEND
```

For tests, inject a fake:

```python
from app.platform.providers.implementations.echo_chat import EchoLLMProvider

llm = EchoLLMProvider(model="test", provider_version="1")
```

### 2. Build neutral messages

```python
from app.platform.providers.contracts.llm import ChatMessage, ChatRole

messages = [
    ChatMessage(role=ChatRole.SYSTEM, content="You are a helpful assistant."),
    ChatMessage(role=ChatRole.USER, content="What is RAG?"),
]
```

Roles and content are **vendor-neutral**. Providers translate to OpenAI JSON, Gemini `contents`, Ollama `messages`, etc.

### 3. Call generate or stream (same signature everywhere)

```python
# Non-streaming
result = await llm.generate(messages, temperature=0.7, max_tokens=2048)
print(result.content)
print(result.usage.input_tokens, result.usage.output_tokens)

# Streaming
async for chunk in llm.stream(messages, temperature=0.7, max_tokens=2048):
    if chunk.delta:
        print(chunk.delta, end="")
```

Return types are always `ChatCompletionResult` / `ChatCompletionChunk` — never vendor SDK objects.

### 4. Switch provider (deployment only)

| Goal | Set |
| ---- | --- |
| Local / tests | `APE_LLM__BACKEND=echo` |
| OpenAI | `APE_LLM__BACKEND=openai` + `APE_LLM__OPENAI_API_KEY` |
| vLLM / LiteLLM | `APE_LLM__BACKEND=openai_compatible` + `APE_LLM__OPENAI_BASE_URL` |
| Ollama | `APE_LLM__BACKEND=ollama` + `APE_LLM__OLLAMA_BASE_URL` |
| Gemini | `APE_LLM__BACKEND=gemini` + `APE_LLM__GEMINI_API_KEY` |

No application code changes — restart the API process after env change (`get_llm_provider` is process-scoped).

---

## How ChatService uses it

`ChatService` receives `BaseLLMProvider` via DI (`dependencies/conversations.py`). It never branches on `backend`:

```text
PromptBuilder → list[ChatMessage]
       ↓
llm.generate(...) or llm.stream(...)
       ↓
ChatCompletionResult → persist assistant message
```

Provider name/model on the result are stored for audit; conversation row holds the config snapshot.

---

## Adding a new LLM backend (checklist)

1. Implement `BaseLLMProvider` in `platform/providers/implementations/<name>_chat.py`.
2. Add enum value to `LLMBackend` in `core/config.py`.
3. Register in `create_llm_provider()` in `llm_factory.py`.
4. Document env vars in `backend/.env.example`.
5. Add contract test with faked HTTP or echo-style fake.

Modules and routes stay unchanged.

---

## Errors

All providers raise `ProviderError` from `platform/providers/errors.py`. Global exception handlers map these to client-safe API errors.

---

## Parity with other providers

| Capability | Contract | Factory | Config key |
| ---------- | -------- | ------- | ---------- |
| Embeddings | `BaseEmbeddingProvider` | `get_embedding_provider()` | `APE_EMBEDDING__BACKEND` |
| Semantic persistence | `ChunkEmbeddingRepository` | request-scoped SQLAlchemy session | PostgreSQL/pgvector |
| Storage | `BaseStorageProvider` | `get_storage_provider()` | `APE_STORAGE__BACKEND` |
| **LLM** | `BaseLLMProvider` | `get_llm_provider()` | `APE_LLM__BACKEND` |

Follow the same **interface → factory → env** pattern for any new integration.

---

## Related

- [Conversation LLM providers](./conversation_llm_providers.md) — backends table
- [Provider architecture](../architecture/provider-architecture.md)
- [RAG journey](./conversation_rag_journey.md)
