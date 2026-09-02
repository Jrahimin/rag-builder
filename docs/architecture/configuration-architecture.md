# Configuration Architecture

> Canonical layout: [module-architecture.md](./module-architecture.md)
> Field-level env map (every key, options, Project overlay): [../configuration-map.md](../configuration-map.md)

## Layers (precedence, lowest → highest)

```text
1. Deployment  — `core.config.Settings` (`APE_*` env vars)
2. Project     — active immutable `project_ai_config_revisions` row
3. Request     — fixed external allowlist only
4. Safety      — deployment bounds and provider/model capabilities
```

`RuntimeConfig` adds the certified production profile, bounded preflight timeouts, and worker
heartbeat/staleness thresholds. `validate_runtime_config` is fail-fast and production-only so
development/test fake providers remain available. Operator configuration serialization uses an
explicit allowlist and credential-presence flags; it never dumps `Settings`.

`ConfigLayer` + `CONFIG_PRECEDENCE_ORDER` in `platform/config/contracts.py`.

`APE_AI_POLICY__DEFAULT_RAG_PROFILE` is the authoritative deployment query-time selection:
`standard`, `quality`, `economy`, or `custom` (`standard` by default). For a preset, the
code-owned bundle supplies every profile-owned execution value and raw retrieval/chat ENV tuning
cannot alter it. Raw execution tuning is used only when the deployment selects `custom`.

Project revisions are sparse, typed documents. Effective resolution records an origin for each
value, the active revision ID/hash, a secret-free global fingerprint, provider-capability version,
and prompt versions. A Project without an active revision inherits the deployment RAG profile and
separate behavior defaults, including `APE_AI_POLICY__SOURCE_POLICY_MODE` for source policy. The deployment cap
`APE_AI_POLICY__SOURCE_POLICY_DEPLOYMENT_CAP` may only lower that mode.

The external request allowlist is `top_k`, an enabled retrieval strategy, existing allowlisted
metadata filters, and explicit `as_of`. Provider/model, temperature, token limits, reranking,
prompts, evidence, citation, and grounding values remain Project policy. Deprecated legacy fields
are observable in `compatibility` mode and fail with `request_policy_override_forbidden` in
`strict` mode (`APE_AI_POLICY__REQUEST_OVERRIDE_MODE`).

## Immutable execution snapshots

Every durable ingestion/indexing job references an immutable,
Project-scoped `JobConfigurationSnapshot`. The snapshot is normalized and
content-addressed by an output SHA-256, includes the parsing/chunking/OCR/embedding/
retrieval values that determine outputs, effective Project AI policy, active config revision/hash,
global fingerprint, provider/prompt versions, active index build, and source-metadata generation.
It deliberately excludes credentials.
Workers combine that snapshot with live deployment secrets. Retries therefore
reproduce the original processing choices without persisting secret material.

Execution and provenance facts, including the observed active index build and source-metadata
generation, do not change snapshot deduplication identity. Corpus `IndexBuild.configuration_hash`
uses a narrower index-artifact identity: chat/LLM policy and retrieval `top_k` do not create a new
content index identity. The worker always restores the staged snapshot before it performs work; it
does not re-resolve changed Project policy from deployment defaults.

Conversation creation similarly appends an immutable `conversation_config_snapshots` row. Messages
reference that row, so later Project or deployment changes cannot alter an existing conversation.
New conversations and jobs resolve the latest code-owned preset definition; standalone retrieval
resolves it immediately. A RAG profile change is query-time only and never requests reprocessing,
re-embedding, reindexing, or rebuilding.
An explicit Super-Admin refresh appends a new snapshot for future messages. Contextual generations
and evaluation runs persist the same secret-free effective configuration and provenance.

## Deliberate exclusions

Project policy does not include embedding models/dimensions or chunking because those values are
coupled to global vector schema and index artifact contracts. Generic key/value configuration is
not used.

## Rules

- Nothing AI-related hardcoded in services
- Project overrides cannot weaken deployment security
- Output-affecting Project revisions and execution snapshots are immutable and hash-addressed
- API and worker composition pass one explicit `Settings` snapshot when wiring a
  service. Provider/config selection does not live inside module services.
