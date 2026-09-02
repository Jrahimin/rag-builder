# Configuration map

Use this page to answer three questions quickly:

1. Which layer owns this value?
2. What is the current default?
3. Will changing it affect future queries, a future index build, or neither?

The complete copy-ready key inventory is in [`.env.example`](../.env.example)
for Docker Compose and [`backend/.env.example`](../backend/.env.example) for a
local Python process. The executable source of truth is:

- `backend/app/core/config.py` — ENV-backed settings and defaults
- `backend/app/core/capability_profiles.py` — deployment capabilities
- `backend/app/platform/config/profiles.py` — RAG, calibration, and index profiles
- `backend/app/platform/config/project_ai.py` — Project configuration and resolver

## Start here

```text
Deployment capability → Index Profile + Calibration Profile
Global RAG profile → Project inherit/preset/Custom selection
  → separate Project behavior
  → safety invariants
```

| Layer | Owner | Where to change it | Scope |
| --- | --- | --- | --- |
| Deployment capability | Deployment selects; code defines | `APE_RUNTIME__CAPABILITY_PROFILE_ID` | Process startup |
| Index / calibration profile | Code | Profile registry | Future index target / query evidence calibration |
| RAG execution values | Code preset or Custom values | Global profile; Project selection | New conversations/jobs; standalone retrieval immediately |
| Project behavior | Project operator | Immutable Project revision | New conversations and jobs |
| Safety invariants | Code | Not operator-configurable | Always enforced |

ENV is for infrastructure, credentials, endpoints, deployment capability,
operational limits, and emergency controls. It is not the normal tuning surface
for retrieval depth, rerank windows, or context size.

## Current defaults

The deployment default is the **Standard** execution profile:
`APE_AI_POLICY__DEFAULT_RAG_PROFILE=standard`. Built-in profiles are selectable
during development while their certification status remains an honest record of
measured evaluation, not a selection gate.

| Setting | Current default | What it means |
| --- | --- | --- |
| Runtime capability | `development` when no explicit capability ID is set | Local/test provider wiring |
| RAG execution values | **Standard** | Balanced retrieval, rerank, context, and history limits |
| Query translation | `false` | Translation is off unless deployment or Project enables it |
| Response mode | `indexed_only` | No web evidence unless a Project opts into a web mode |
| Retrieval strategy | `hybrid` | Semantic + keyword retrieval |
| Rerank mode | `always` | Rerank is attempted; provider failure falls back safely |
| Source policy | `enforce` when governed source metadata exists | Ungoverned Projects remain neutral |

### Standard execution values

| Area | Default |
| --- | --- |
| Semantic / keyword candidates | `50` / `50` |
| HNSW search effort | `100` |
| Fusion | `rrf_k=60`, semantic weight `1.0`, keyword weight `1.0` |
| Rerank | `always`, window `25`, return `8` |
| Retrieval filters | score, rerank-score, and OCR-confidence thresholds: `0.0` |
| Diversity | 4 chunks/document, 2 chunks/section, content-hash deduplication on |
| Passage scoring | off; window/overlap/minimum: `96` / `24` / `32` tokens |
| Relationship expansion | 8 related sources, 20 relationship candidates |
| Answer retrieval | `top_k=10` |
| Context | `8` chunks, `12,000` characters |
| History | `20` messages |

## Minimal hosted environment

This is the practical minimum for the Docker Compose hosted-managed setup. Copy
it to the repository-root `.env`, replace every placeholder, then add optional
keys from [`.env.example`](../.env.example) only when needed. Compose supplies
the internal database, Redis, and MinIO hostnames from these credentials.

```dotenv
# Application and capability
APE_APP__ENV=production
APE_RUNTIME__CAPABILITY_PROFILE_ID=hosted-managed
APE_LOGGING__LEVEL=INFO
APE_CORS__ALLOW_ORIGINS=https://console.example.com

# Required application secrets
APE_AUTH__ENABLED=true
APE_AUTH__ADMIN_JWT_SECRET=<long-random-secret>
APE_AUTH__KEY_PEPPER=<long-random-secret>
APE_WEBHOOKS__SIGNING_KEY=<long-random-secret>

# Docker Compose data services
POSTGRES_USER=postgres
POSTGRES_PASSWORD=<strong-database-password>
POSTGRES_DB=rag_builder
REDIS_PASSWORD=<strong-redis-password>
MINIO_ROOT_USER=ragbuilder
MINIO_ROOT_PASSWORD=<strong-minio-password>
MINIO_BUCKET=ape-artifacts
APE_STORAGE__BACKEND=minio

# Hosted AI providers
APE_LLM__BACKEND=openai
APE_LLM__MODEL=gpt-5.6-luna
APE_LLM__OPENAI_API_KEY=<openai-api-key>
APE_COHERE__API_KEY=<cohere-api-key>

# Hosted OCR capability
APE_OCR__ENABLED=true
APE_OCR__BACKEND=google_vision
APE_OCR__BANGLA_BACKEND=google_vision
APE_OCR__GOOGLE_API_KEY=<google-vision-api-key>

# Handy safe defaults
APE_QUERY_TRANSLATION__ENABLED=false
APE_RETRIEVAL__AUTO_BUILD_AFTER_PROCESS=true
APE_AI_POLICY__DEFAULT_RAG_PROFILE=standard
APE_AI_POLICY__REQUEST_OVERRIDE_MODE=strict
APE_AI_POLICY__SOURCE_POLICY_DEPLOYMENT_CAP=enforce
```

For a local Python process, start from [`backend/.env.example`](../backend/.env.example):
use `APE_RUNTIME__CAPABILITY_PROFILE_ID=development`, hash embeddings, `noop`
reranking, local storage, and no provider credentials.

## Profiles

Profiles have simple development-stage IDs. Preset content is code-owned and may be refined;
new work receives the latest definition. `custom` is an explicit stored selection whose
individual execution values are authoritative.

| RAG profile | Intended use | Status | Normal Project API/UI |
| --- | --- | --- | --- |
| `standard` | Balanced default values shown above | Candidate until measured evaluation certifies it | Selectable in development |
| `quality` | Wider retrieval/rerank effort and larger context | Candidate until measured evaluation certifies it | Selectable in development |
| `economy` | Narrower retrieval/rerank effort and smaller context | Candidate until measured evaluation certifies it | Selectable in development |

The hosted certification manifest requires named/versioned suites: `tax@v1`
at 21/21, `ci-smoke@v1`, and `cross-lingual-quality@v2`. The certification engine
is generic: another deployment can supply a different manifest without any tax
logic in the engine.

```text
Inherit
  → apply the global profile

Select Standard / Quality / Economy
  → apply the exact current code-owned bundle

Change a profile-owned value
  → materialize current effective values and switch to Custom

Select a preset again
  → clear Custom execution values and apply the preset
```

Behavior fields do not change the RAG profile. Advanced execution controls are
editable only in Custom. Preset and inherit revisions store only their selection;
a Custom revision persists a complete explicit execution bundle. Conversation/job
provenance stores the resolved ID, profile hash, and effective values.

| Capability ID | Generation / retrieval capability | Calibration profile | Index profile |
| --- | --- | --- | --- |
| `development` | Local/test wiring | `hash-local-whole-chunk` | `development-hash` |
| `hosted-managed` | OpenAI generation, Cohere embedding/rerank, Google Vision OCR | `cohere-v4-managed-whole-chunk` | `hosted-cohere-v4` |
| `hosted-openai` | OpenAI generation/embedding, Cohere rerank | `openai-large-cohere-whole-chunk` | `hosted-openai-large` |
| `private-ollama` | Ollama generation/embedding, lexical rerank | `ollama-1024-local-whole-chunk` | `private-ollama-1024` |

## Project configuration

Project behavior is sparse and immutable: omit a behavior field to inherit it.
Execution is different: `inherit` and presets store only a selection, while
Custom persists every execution field so it cannot inherit from a later global
profile. A translation-only override is valid and persists:

```json
{
  "behavior": { "translation_policy": "enabled" },
  "execution": {}
}
```

### Behavior fields

| Field | Options | Inherited default | Effect |
| --- | --- | --- | --- |
| `behavior.response_mode` | `indexed_only`, `indexed_then_web`, `indexed_and_web` | `indexed_only` | Controls when web evidence is used; deployment capability still applies. |
| `behavior.grounding_assurance` | `strict`, `balanced` | Deployment setting | Bounded Project grounding posture. |
| `behavior.domain_instructions` | Text, max 20,000 chars | Empty | Project-specific standing instructions. |
| `behavior.translation_policy` | `inherit`, `enabled`, `disabled` | `inherit` → OFF | Enables/disables query-only translation. Original query always remains available. |
| `behavior.generation_model_id` | Deployment-allowlisted logical ID | Deployment default | Selects an approved model identity; raw model strings are rejected. |

### Execution fields

| Field group | Options / bounds | Default | Effect |
| --- | --- | --- | --- |
| `execution.profile_id` | `inherit`, `standard`, `quality`, `economy`, `custom` | `inherit` | Presets apply the exact current code-owned bundle. Custom is a complete persisted bundle. |
| Candidate depth | `semantic_candidate_top_k`, `keyword_candidate_top_k`: 1–200 | 50 / 50 | Candidates before fusion and reranking. |
| ANN / fusion | `hnsw_ef_search`: 1–1000; `rrf_k`: 1–500; weights: 0–10 | 100 / 60 / 1.0 | Recall and fusion balance. |
| Filters / rerank | score, rerank-score, OCR-confidence thresholds: 0–1; `rerank_mode`: `always` or `cross_language`; window/return: 1–100 | 0 / 0 / 0; always / 25 / 8 | Retrieval admission and rerank effort. Return cannot exceed window. |
| Diversity / passages / relationships | Per-document/section caps: 1–100; content-hash deduplication; passage scoring/token bounds; related-source and relationship-candidate caps | 4 / 2 / on / off / 8 / 20 | Result diversity, scoring, and relationship expansion. |
| Answer context | `retrieval_top_k`: 1–100; chunks: 1–50; chars: 500–200,000 | 10 / 8 / 12,000 | Evidence sent to generation. |
| History | `max_history_messages`: 0–200 | 20 | Conversation history included in generation. |

Projects cannot choose provider credentials/endpoints, raw calibration thresholds,
citation enforcement, source-policy mode, index profiles, or embedding identity.

## Deployment ENV reference

All application keys use `APE_` and `__` nesting. For example,
`retrieval.auto_build_after_process` becomes
`APE_RETRIEVAL__AUTO_BUILD_AFTER_PROCESS`. Lists are comma-separated.

### Connectivity, secrets, and operations

| ENV key / family | Default | What it controls |
| --- | --- | --- |
| `APE_RUNTIME__CAPABILITY_PROFILE_ID` | unset → `development` | Capability selection. Values: `development`, `hosted-managed`, `hosted-openai`, `private-ollama`. |
| `APE_AI_POLICY__DEFAULT_RAG_PROFILE` | `standard` | Deployment query-time profile: `standard`, `quality`, `economy`, or `custom`. Presets ignore raw profile-owned ENV tuning. |
| `APE_DATABASE__*` | localhost / 5432 / `ape` | Database host, port, user, password, name, pool settings. |
| `APE_REDIS__*` | localhost / 6379 | Cache and job broker connection. |
| `APE_STORAGE__*`, `APE_MINIO__*` | local storage | Object-storage backend and connection. |
| `APE_LLM__*` | `echo`, `gpt-4o-mini` locally | Generation provider, model, API key, endpoint, timeout. |
| `APE_EMBEDDING__*` | `hash`, `text-embedding-3-large`, 1024 | Embedding provider, model, dimensions, endpoint, batch size. |
| `APE_COHERE__*`, `APE_RERANKER__*` | Cohere endpoint, `rerank-v4.0-pro` | Cohere credentials/endpoint and reranker connection. |
| `APE_WEB_SEARCH__*` | compatible LLM connection | Optional web-search provider and connection; `disabled` is a kill switch. |
| `APE_OCR__*` | disabled / `noop` locally | OCR capability, credentials, endpoint, quality limits. |
| `APE_AUTH__*`, `APE_WEBHOOKS__*` | development-safe local defaults | Authentication, rate limits, session secrets, signed callbacks. |
| `APE_JOBS__*`, `APE_LOGGING__*`, `APE_CORS__*` | local defaults | Worker dispatch/retries, logs, browser access. |

### Index-producing settings

| ENV family | Typical default | Impact when changed |
| --- | --- | --- |
| `APE_PARSING__*` | PDF parsers `pymupdf,pdfium` | Parsed output; requires reprocess. |
| `APE_OCR__ENABLED`, OCR backend/language/quality settings | `false`, `noop`, `en` locally | OCR output; requires reprocess. Credentials/endpoints/timeouts do not change index identity. |
| `APE_CHUNKING__STRATEGY` | `auto` | Chunk boundaries; requires reprocess. |
| `APE_CHUNKING__TARGET_TOKENS`, `MAX_TOKENS`, `MIN_TOKENS`, structure thresholds | 250 / 400 / 50 | Chunk boundaries; requires reprocess. |
| `APE_EMBEDDING__BACKEND`, `MODEL`, `DIMENSIONS` | hash / text-embedding-3-large / 1024 locally | Embedding identity; requires a new embedding set and rebuild. |
| `APE_RETRIEVAL__EMBEDDING_SET_VERSION` | 2 locally, 3 hosted-managed | Selects the target embedding set. |
| `APE_RETRIEVAL__FTS_REGCONFIG` | `simple` | Keyword index identity; requires reindex. |
| `APE_RETRIEVAL__FILTERABLE_METADATA_KEYS` | `source,tags,ocr_confidence` | Materialized filter schema; reindex/rebuild as classified. |

`IndexArtifactConfig` decides the exact action: `none`, `reprocess`, `reembed`,
`reindex`, or `rebuild`. Profile labels and hashes are provenance only; changing
an equivalent label does not change the materialized fingerprint. No configuration
write queues processing or rebuild work by itself.

### Defaults, policy caps, and emergency controls

| ENV key | Default | Use |
| --- | --- | --- |
| `APE_RETRIEVAL__AUTO_BUILD_AFTER_PROCESS` | `true` | Automatically build after successful processing. |
| `APE_RETRIEVAL__STRATEGY` | `hybrid` | Deployment fallback retrieval strategy. |
| `APE_RETRIEVAL__RERANK_MODE` and profile-owned retrieval/chat execution keys | registry value under a preset | Raw execution inputs only when the global profile is `custom`; Project Custom values are persisted instead of inheriting them. |
| `APE_AI_POLICY__DEFAULT_RAG_PROFILE` | `standard` | Authoritative deployment query-time profile. |
| `APE_QUERY_TRANSLATION__ENABLED` | `false` | Deployment translation default; Project can inherit/on/off. |
| `APE_AI_POLICY__DEFAULT_GENERATION_MODEL_ID` | capability-specific | Default logical generation model. |
| `APE_AI_POLICY__ALLOWED_GENERATION_MODEL_IDS` | capability-specific | Exact Project model allowlist. |
| `APE_AI_POLICY__MAX_REQUEST_TOP_K` | `100` | Request safety cap. |
| `APE_AI_POLICY__REQUEST_OVERRIDE_MODE` | `strict` | Deprecated request override handling. |
| `APE_AI_POLICY__SOURCE_POLICY_MODE` | `enforce` | Governance default when source metadata exists. |
| `APE_AI_POLICY__SOURCE_POLICY_DEPLOYMENT_CAP` | `enforce` | Emergency cap; may lower but never raise enforcement. |
| `APE_RETRIEVAL__MODIFIES_EXPANSION_MODE` | `expand` | Code invariant for canonical Project resolution; retained as a legacy/deployment fallback setting. |
| `APE_CHAT__EVIDENCE_GATE_MODE` | `enforce` | Evidence-admission rollout/emergency posture. |

## Safety and reproducibility

- Reranker failures and timeouts fail open to safe fused retrieval order; queries
  remain available.
- Translation defaults OFF and falls back to the original query on failure.
- Source policy and `MODIFIES` behavior apply only to governed source metadata and
  relationships. Ungoverned Projects remain neutral.
- Query-time and Project changes do not alter index identity or queue a rebuild.
- Conversations, jobs, revisions, and active indexes record effective values,
  hashes, profile/calibration/index provenance, and resolver identity so they remain
  reproducible after later configuration changes.
