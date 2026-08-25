# Contextual Generation API

Generate validated LLM output from context supplied by an external application.
This path does not upload documents, create embeddings, or call retrieval.

**Prefix:** `/api/v1/projects/{project_id}/generations`

Every endpoint requires the existing Organization API key and verifies that the
Project belongs to that Organization.

## Registered use cases

| Use case | Prompt versions | Default output schema |
| --- | --- | --- |
| `contextual_answer` | `v1`, `v2` | Non-empty string |
| `structured_summary` | `v1` | `{summary: string, key_points: string[]}` |

Callers select a use case; they cannot send a system prompt. A valid
`response_schema` may replace the use case's default schema while the registered
prompt remains authoritative.
Free-text schema annotations are not forwarded to the model.

## POST ``

Runs one synchronous contextual generation. Returns **201**. Send an
`Idempotency-Key` header when the caller may retry.

```http
POST /api/v1/projects/{project_id}/generations
Authorization: Bearer ape_live_...
Idempotency-Key: invoice-4821-v1
Content-Type: application/json
```

```json
{
  "use_case": "contextual_answer",
  "input": {
    "question": "Is this invoice ready for approval?"
  },
  "context": {
    "invoice": {
      "number": "INV-4821",
      "subtotal": 950,
      "tax": 50,
      "total": 1000
    },
    "checks": [
      {"name": "purchase_order_match", "passed": true},
      {"name": "budget_available", "passed": true}
    ]
  },
  "prompt_version": "v2",
  "response_schema": {
    "type": "object",
    "properties": {
      "decision": {"type": "string", "enum": ["approve", "review"]},
      "reason": {"type": "string"},
      "invoice_total": {"type": "number"}
    },
    "required": ["decision", "reason", "invoice_total"],
    "additionalProperties": false
  },
  "locale": "en-US",
  "generation_config": {
    "temperature": 0.1,
    "max_tokens": 500
  },
  "retention": "none"
}
```

`context` accepts non-empty text, an object, or an array, so mixed text and
structured context can be sent as array items. Context size, request size,
nesting depth, node count, schema size, and schema validity are checked before
the provider is called.

`generation_config` retains deprecated `provider`, `model`, `temperature`, and
`max_tokens` fields for the migration window. Compatibility mode records and snapshots explicit
use; strict mode rejects it with `request_policy_override_forbidden`. Effective provider/model and
generation parameters come from Project policy and are validated against the versioned capability
descriptor before the provider is called. The generation trace persists the secret-free effective
configuration hash and provenance.

Retention modes:

| Mode | Stored input/context |
| --- | --- |
| `none` | No raw payload; only byte counts and SHA-256 identities |
| `metadata_only` | No raw payload; hashes plus top-level shape metadata |
| `full` | Full input and context, only when deployment policy allows it |

The safe deployment default is `none`.

**Response `data`:**

```json
{
  "id": "bb0e8400-e29b-41d4-a716-446655440006",
  "project_id": "660e8400-e29b-41d4-a716-446655440001",
  "use_case": "contextual_answer",
  "status": "succeeded",
  "output": {
    "decision": "approve",
    "reason": "Both supplied approval checks passed.",
    "invoice_total": 1000
  },
  "grounded": true,
  "grounding_status": "context_supplied",
  "source_provenance": "none",
  "context_provenance": "caller_context",
  "web_enrichment_used": false,
  "resolved_chat_response_mode": "indexed_only",
  "provider": "openai",
  "model": "gpt-4o-mini",
  "provider_version": "1",
  "prompt_version": "v2",
  "schema_version": "custom-2fd8f506ad6e3a91",
  "usage": {
    "input_tokens": 310,
    "output_tokens": 43,
    "total_tokens": 353
  },
  "timing": {
    "provider_ms": 740,
    "total_ms": 752
  },
  "request_id": "req_...",
  "trace_id": "trace_...",
  "retention": "none",
  "payload_retained": false,
  "idempotency_replayed": false,
  "finish_reason": "stop",
  "failure": null,
  "created_at": "2026-07-26T12:00:00Z",
  "completed_at": "2026-07-26T12:00:01Z"
}
```

`grounded=true` means the registered prompt constrained generation to the
caller-provided context. It is not a semantic entailment score.

Contextual generation never applies chat RAG fallback semantics. Caller-supplied context remains
authoritative even when the resolved Project chat policy enables web modes. The response and
persisted provenance explicitly report that no indexed/web evidence or web enrichment was used.

Reusing the same idempotency key with the same normalized request returns the
original generation ID and sets `idempotency_replayed=true`. Reusing it with a
different request returns `409 generation_idempotency_conflict`.

Stable errors include:

| HTTP | Code | Meaning |
| --- | --- | --- |
| 400 | `unknown_generation_use_case` | Use case is not registered |
| 400 | `unknown_generation_prompt_version` | Prompt version is not registered for the use case |
| 409 | `generation_idempotency_conflict` | Key was used for another request |
| 409 | `generation_in_progress` | Same idempotent request is still running |
| 413 | `generation_payload_too_large` | Whole request exceeds its limit |
| 413 | `generation_context_too_large` | Context exceeds its limit |
| 422 | `generation_context_invalid` | Context nesting or structure is invalid |
| 422 | `generation_response_schema_invalid` | JSON Schema is invalid or uses an external `$ref` |
| 502 | `generation_output_schema_mismatch` | Provider output failed schema validation |
| 503 | `llm_provider_unavailable` | Configured provider could not complete the request |

## GET `/{generation_id}`

Returns the persisted normalized output, status, usage, timing, versions, and
the original create request/trace IDs. Raw retained input and context are never
returned by this endpoint.

Unknown and wrong-Project IDs both return `404 generation_not_found`.
