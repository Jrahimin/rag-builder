# Project AI Policy and Reproducible Provenance

Phase 1 makes Project ownership and AI behavior explicit without changing the
Organization → Project isolation boundary.

## Policy resolution

Effective AI configuration resolves in this order:

1. deployment defaults;
2. the active immutable Project revision;
3. the fixed request allowlist;
4. deployment bounds and provider/model capability validation.

The typed revision covers LLM provider/model/generation values, retrieval and reranking defaults
(`rerank_mode`: Inherit / Always / Cross-language / Off; query translation Inherit / On / Off),
chat context/history/citation/grounding behavior, domain instructions, prompt profile/version, and
source-policy rollout mode (`APE_AI_POLICY__SOURCE_POLICY_MODE`, default `off`; Project leaf
overrides it; `APE_AI_POLICY__SOURCE_POLICY_DEPLOYMENT_CAP` may only lower the result). Embedding
and chunking settings remain deployment/index-artifact
policy. All-inherit Create Project and AI Configuration submits create no revision. If Project
create succeeds and the optional AI-config save fails, the Project is kept on inherited defaults
and the console offers a retry on AI Configuration.

Chat policy also contains sparse `response_mode`: `indexed_only` (global default),
`indexed_then_web`, or `indexed_and_web`. Sparse chat grounding policy may set `grounding_mode`
(`strict` default, `balanced`) and `high_confidence_reranker_evidence_score` without changing
medium-confidence bars. Existing revisions omit these fields and continue to inherit `strict`.
Web-enabled revisions require a configured deployment
search provider and the v5 source-aware prompt. Sparse `web_search` policy can enable or disable
web use per Project and bound its model, result count, evidence-character budget, output-token
budget, and timeout. Provider credentials and endpoint remain deployment-owned. When no dedicated
web provider/model is configured, compatible `APE_LLM__OPENAI_*` settings and the resolved Project
LLM model are inherited. Existing revisions omit these fields and continue to inherit
`indexed_only`.

Only Super Admin operator endpoints can read or write revisions. Writes use an expected active
revision ID for optimistic concurrency. History is append-only, and restore copies an old payload
into a new revision.

Operator edits start from the stored active sparse revision, then merge the fields rendered by the
form. This preserves Project-owned values not exposed by a particular form view (for example chat
budgets, prompt profile/version, or reranking limits). The console fails visibly if it cannot load
the active revision instead of submitting an empty base payload.

## Provenance boundary

- Conversation creation stores a secret-free effective snapshot. Both sides of every chat turn
  reference the active snapshot.
- An administrative conversation refresh appends a snapshot and changes only future messages.
- Contextual generation persists its effective policy and prompt/schema versions.
- Evaluation runs persist the same policy and stage it into their durable job snapshot.
- Durable workers reconstruct output-affecting settings from the staged snapshot and merge only
  live credentials from deployment settings.

Recorded provenance includes Project revision ID/hash, global config fingerprint,
provider-capability version, prompt versions, active index build when relevant, and the captured
source-metadata generation. Retrieval diagnostics also record the configured and effective source
policy modes plus any deployment-cap override. Snapshot deduplication excludes execution and
observed provenance facts; index-build identity further excludes runtime chat/LLM policy and
retrieval result-count defaults.

## Ownership migration

New Projects require an explicit Organization boundary and are immediately locked. The migration
marks only pre-existing Projects under the legacy/default Organization as unlocked. Super Admins
can run a count-only preflight, then either reassign once or confirm the current Organization. The
operation preserves the Project ID and all Project-scoped foreign keys; its audit payload records
both previous and target Organization IDs.

## Implemented follow-on lifecycle and retrieval policy

Source revisions and activation history now provide the immutable Project source-generation
boundary. Source-aware retrieval, durable source citation snapshots, and Operator usage reporting
reuse the same Project/config/index/source provenance captured by conversations, generations,
jobs, and evaluations. Natural-language temporal inference, per-Project embedding or chunking
policy, billing, and quotas remain intentionally outside this contract.


The additive `execution.execution_policy` field selects `legacy` or `adaptive_v1`
for future immutable revisions. Omitted historical fields resolve to `legacy`,
independent of later deployment defaults. The value is materialized in effective
execution settings and recorded in configuration provenance; changing it requires
the ordinary revision and conversation snapshot refresh workflow. See
`conversation_module.md` for reserves, promotion and correction allowances.

Measured adaptive reserves use project-scoped completed Message samples from
the exact immutable configuration hash and budget class. A conversation snapshot
ID resolves that hash; evaluation uses its captured effective configuration hash.
Estimates freeze once, require at least twenty qualifying samples, and record
bounded sample provenance. Refreshing policy remains the existing explicit
immutable revision/snapshot operation; this correction activates no policy or build.

### Selecting the message execution policy

Project AI settings expose **Message execution policy** separately from retrieval
profiles and bounded recovery. Choosing Adaptive saves `execution.execution_policy`
as `adaptive_v1` in a new immutable revision; Use Global removes the override.
Legacy remains the default. Simple requests have a 45-second hard ceiling and
30-second p95 target; complex requests have a 120-second ceiling and 90-second p95
target. The existing admission/reserve policy bounds recovery and can promote a
required recoverable dependency once. No ceiling is increased by this UI control.
Existing conversations retain their saved configuration snapshot. Start a new
conversation after saving a revision to exercise the new policy. Configuration
selection and code checks do not prove live acceptance or source applicability.
