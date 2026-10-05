# Provider output contracts

Structured output is a syntax contract followed by local schema and factual
verification. Strict schema mode requires an explicit configured endpoint/model
attestation in `llm.schema_capabilities`: provider, model, credential-free
endpoint hash, `schema_mode=json_schema`, reviewer, evidence hash and capability
revision. No vendor/model certification is supplied by this offline implementation.

Unknown pairs use the portable schema prompt. The existing official OpenAI
JSON-object baseline remains JSON-object mode; custom compatible endpoints do
not inherit it. Gemini and Ollama strict schemas likewise require the exact
configured pair. All fallback outputs still pass the stage's local Pydantic and
semantic/proof validation. There is no hidden capability-error retry loop.

Observed request telemetry records effective purpose, model, reasoning and schema
mode/hash. The application's existing Luna low-reasoning stage policy is retained;
the Codex implementation/review agent setting does not change application routing.
Credentials, URL query secrets, response bodies and hidden reasoning are excluded.
Declarations are captured in new secret-free immutable job snapshots; omitted
legacy declarations default to the portable baseline and are not retroactively
certified. Live paid-provider certification and model-setting comparison remain
pending. Keep the current provider/model and embedding configuration as baseline.

Controlled offline wire fixtures may explicitly declare JSON-object capability
for an exact endpoint/model pair. Such a fixture declaration does not certify a
vendor endpoint. Strict-schema and fallback tests exercise actual adapter request
bodies, local validation and durable Message provenance using MockTransport only.
