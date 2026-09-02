# Configuration map

This is the Phase-2 configuration contract. The executable, field-complete
catalog is [`backend/app/platform/config/catalog.py`](../../backend/app/platform/config/catalog.py);
it classifies every applicable Settings, Project, resolver, request, and
snapshot leaf by owner, lifecycle, audience, impact, timing, compatibility,
and replacement. ENV is deployment configuration, not the normal AI-tuning
surface.

## Ownership and precedence

| Surface | Owner | Lifecycle | Effect |
| --- | --- | --- | --- |
| Code invariants and provider adapters | Code | Release | Immediate after deployment |
| Credentials, endpoints, infrastructure | Deployment | Process startup | Restart required |
| Capability, calibration, RAG execution, and index profiles | Code | Append-only versioned registry | Selected by immutable ID |
| Parsing, OCR, chunking, embedding, FTS/materialized metadata | Deployment index artifact | New build only | Explicit reprocess/re-embed/reindex classification |
| Project V2 behavior and sparse execution | Project | Immutable revision | New conversations/snapshots only |
| `top_k`, allowlisted metadata filters, `as_of` | Request | One request | Immediate |
| Conversation and job resolution | Snapshot | Immutable | Never drifts |

Resolution is: code invariants → deployment profiles → connectivity Settings → active Project revision →
supported request allowlist → code-owned invariants. Secrets are never stored
in Project revisions or snapshots. `effective_value_hash` identifies resolved
values; `resolution_fingerprint` additionally identifies the resolver, schema,
registry, and source revision used to obtain them.

## Canonical Project V2 contract

New writes use only this shape:

```json
{
  "behavior": {
    "response_mode": "indexed_only",
    "grounding_assurance": "strict",
    "domain_instructions": "Use the organization terminology.",
    "translation_policy": "inherit",
    "generation_model_id": "deployment-default"
  },
  "execution": {
    "profile_id": "standard@v1",
    "retrieval_top_k": 10,
    "rerank_mode": "always",
    "rerank_candidate_window": 25,
    "rerank_return_count": 8
  }
}
```

Every field is optional/sparse. `execution.profile_id` selects an immutable,
certified RAG Execution Profile; remaining execution values are Advanced sparse
overrides. On persistence, the service adds and later verifies
`execution.profile_hash`; clients do not invent this value. `economy@v1`,
`standard@v1`, and `quality@v1` begin as candidates, so
normal Project UI/API cannot select or recommend them until certification metadata
is checked in. Candidate execution is available only through an explicit Test Lab path.

Deployment Capability Profiles select an exact generation-model allowlist, an
Evidence Calibration Profile, an optional certified default RAG profile identity, feature flags, and
a default Index Profile. Index Profiles remain separate from query-time execution.

Projects may control response behavior, grounding assurance, instructions,
translation, an approved logical generation model, and canonical advanced
execution fields. They cannot control providers, raw model names, credentials,
web budgets/timeouts, raw calibration, citation/evidence/invariant switches,
source-policy switches, or reranker disablement.

The Project model chooser lists only exact deployment-approved logical IDs from
the code-owned generation registry and capability profile. Arbitrary raw model
strings and commercial tier labels are rejected. Custom deployments register a
logical ID rather than accepting free-form Project model input.

## Safety and governance behavior

V2 resolution enforces hybrid retrieval, enabled hosted reranking, evidence
enforcement, durable citations/provenance, content-hash deduplication, and
governed-source behavior. Candidate-wise grounding remains an internal,
code-owned technique; it is not a universal invariant or Project setting.

Reranker provider errors and timeouts remain fail-open: retrieval falls back to
its safe RRF order and the query remains available. “Enabled” means the stage
is attempted, not that a transient provider incident makes chat unavailable.

Source governance and `MODIFIES` expansion apply automatically when source
metadata/relationships exist. Projects with ungoverned source documents remain
neutral; migration alone does not exclude their documents.

Query translation defaults to **OFF**. A V2 Project can explicitly enable it
through `behavior.translation_policy`.

## Index artifact and lifecycle

`IndexArtifactConfig` is the typed, versioned identity for materialized output:

- parsing behavior;
- OCR behavior (not credentials, endpoints, retries, or timeouts);
- chunking behavior (not semantic batching);
- embedding backend/model/dimensions (not credentials, endpoint, or batch size);
- FTS configuration and materialized metadata schema.

Query-time controls, web settings, generation settings, and Project V2 changes
do not change an index artifact. No configuration migration schedules document
processing, embedding, indexing, or a rebuild. If an explicit artifact target
changes, the service reports `none`, `reprocess`, `reembed`, `reindex`, or
`rebuild` before activation. New explicitly-profiled manifests record Index
Profile ID/hash plus artifact fingerprint/version; old manifests and active
pointers retain their historical identity unchanged. RAG profile or Project
behavior changes never alter index artifact identity or queue corpus work.
Index Profile ID/hash are descriptive pinned provenance and are deliberately
excluded from the materialized artifact fingerprint; assigning an equivalent
profile label alone cannot request a rebuild.

The separate `FutureProjectIndexSelection` contract remains intentionally hidden
from normal UI. It reserves a Super Admin `index_profile_id` path for a later,
explicitly impact-reviewed selector without coupling it to RAG execution.

## Phase-2 profile normalization

Existing V2 revisions remain readable and active without rewriting. Super Admin
can preview or append an equivalent profile-backed/Custom V2 revision with:

```text
GET  /api/v1/operator/projects/{project_id}/ai-config/normalize-profile
POST /api/v1/operator/projects/{project_id}/ai-config/normalize-profile
```

Normalization is append-only, reports the effective diff, and never queues
reprocessing, re-embedding, reindexing, or rebuild work.

## V1 historical compatibility and normalization

Existing rows have `schema_version=1` and remain immutable/readable exactly as
historical V1 payloads. Old conversation/job/index snapshots likewise retain
their original values and hashes. V1 is a reader compatibility path, not a
normal new-write format.

New edits and ordinary restore create V2 revisions. Restoring V1 creates a V2
copy rather than reactivating legacy live semantics. Super Admin can use:

```text
GET  /api/v1/operator/projects/{project_id}/ai-config/normalize-v1
POST /api/v1/operator/projects/{project_id}/ai-config/normalize-v1
```

The GET is a read-only preview of the V2 payload, concise effective diff,
warnings, and required index action. The confirmed POST takes the expected
active revision ID and audit reason, appends a V2 revision with source metadata,
and activates it. It never queues index work by itself. Deployment-wide
`GET /api/v1/operator/ai-config/normalization-status` reports the active V1
Project count for rollout tracking.

## Retired inputs and compatibility bridge

These are rejected for new Settings/Project V2 input with migration guidance:

| Retired input | Replacement |
| --- | --- |
| `chunking.overlap_tokens` | Remove it; it never changed output. |
| `chunking.strategy=recursive_character` | `recursive_fallback` |
| `retrieval.auto_embed`, `auto_index` | `retrieval.auto_build_after_process` |
| `rerank_enabled`, `rerank_top_n`, `rerank_return_n` | `rerank_mode`, `rerank_candidate_window`, `rerank_return_count` |
| `modifies_expansion_enabled` | `modifies_expansion_mode` (internal) |
| `chat.retrieval_top_k` | `retrieval.default_top_k` |
| ENV `provider_version` | Code-owned adapter implementation versions |
| `retrieval.language_metadata_schema_version` | Code-owned language metadata schema identity |
| V1 provider/model/web/calibration/source/citation controls | V2 behavior/execution plus code invariants |

Historical V1 readers and old job/snapshot readers adapt these values without
rewriting their persisted JSON. Request compatibility is deployment-controlled;
strict mode is the default and rejects deprecated request policy overrides,
while the temporary compatibility bridge emits diagnostics.

## Deployment variables that matter to this contract

Use `APE_` with `__`, for example
`APE_RETRIEVAL__AUTO_BUILD_AFTER_PROCESS=false`. Important canonical keys:

| Path | ENV key | Owner |
| --- | --- | --- |
| `runtime.capability_profile_id` | `APE_RUNTIME__CAPABILITY_PROFILE_ID` | Code profile selection |
| `retrieval.auto_build_after_process` | `APE_RETRIEVAL__AUTO_BUILD_AFTER_PROCESS` | Deployment workflow |
| `retrieval.modifies_expansion_mode` | `APE_RETRIEVAL__MODIFIES_EXPANSION_MODE` | Code-governed execution |
| `query_translation.enabled` | `APE_QUERY_TRANSLATION__ENABLED` | Deployment default (OFF) |
| `ai_policy.request_override_mode` | `APE_AI_POLICY__REQUEST_OVERRIDE_MODE` | Request bridge (strict) |

Normal candidate depth, rerank windows, context budgets, and generation allowlists
are profile-owned rather than ENV defaults. ENV continues to own credentials,
endpoints, operational limits, and provider wiring. Explicit provider/model values
in deployment templates must match the selected capability and index profiles.

See the environment examples for infrastructure, credentials, provider
endpoints, parsing/OCR/chunking, and complete deployment configuration.
