# Provider expense controls and API behavior

The deployment controls are documented in [provider_cost_controls.md](../features/provider_cost_controls.md). No new public billing endpoint or request field is added; operator accounting is available through the read-only `provider-costs` CLI.

- Project requests attach an accounting reference for their entire lifetime, including SSE delivery. Durable jobs attach their job ID. Evaluation jobs use the evaluation budget and require paid-evaluation opt-in and enforced finite budgets before Cohere dispatch.
- With budget enforcement enabled, attempts reserve spending before external dispatch. A failed admission produces nonretryable `provider_budget_exhausted` on [jobs](jobs_api.md). Manual retry uses the existing retry endpoint after the operator reviews limits. Standard provider error handling still applies to synchronous retrieval/chat and reranker degradation; no new HTTP status contract is introduced.
- With ingestion debounce enabled, automatic embedding work can appear as a project-level `corpus.reembed` job with no document ID, shared by multiple completed document-processing jobs. Documents stay chunked until the corpus build validates and activates. Poll document status and project jobs rather than assuming one embedding job per upload. Explicit manual build APIs retain their existing semantics.
- Startup readiness reports Cohere probes as `skipped` and capability unverified when accounting is enabled. Core dependency checks continue. A scoped project operation is required for a live provider verification.
- Purging a document clears its project's reusable vector cache; deleting the project clears cache rows. Secret-free usage attempts survive hard deletion to preserve financial accounting.

Configuration flags are operator-owned environment settings, not mutable project AI policies or API caller options. Separate deployments have separate recorded budgets. The limits cover recorded Cohere work from the activated deployment, not all usage billed to the same Cohere account.
