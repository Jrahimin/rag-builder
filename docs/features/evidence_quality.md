# Evidence Quality and Grounded Answers

## Purpose

Phase 4 turns retrieval and grounding into reproducible product behavior. A Project owns immutable
evaluation dataset versions and append-only quality runs. Every run records the exact dataset,
indexed-corpus fingerprint, chunking/retrieval/chat configuration, embedding/model/provider
versions, prompt version, evaluator version, and a configuration hash.

## Architecture

```text
Evaluation API → EvaluationService → JobRun + snapshot + outbox
                                      ↓ worker
                           EvaluationRunnerService
                              ↓ ports (composition)
       SearchService profiles + chat Context/Prompt/Grounding services
                              ↓
          evaluation_runs metrics, cases, regressions, comparison
                              ↓
                   Operator Evidence Quality view
```

`modules/evaluation/` owns datasets, runs, metric calculation, thresholds, and comparison rules. It
does not import retrieval or conversation internals. `composition/evaluation.py` adapts the existing
`SearchService`, context builder, prompt builder, grounding service, and provider contracts.
Evaluations execute through the durable job runtime as `evaluation.run`; HTTP only stages work.

## Dataset contract

Dataset versions are immutable and content-addressed. Cases cover `exact_token`, `paraphrase`,
`metadata_filter`, `multilingual`, `no_answer`, and `citation`. Answerable cases identify relevant
chunk or document IDs. Optional expected answer tokens measure practical answer coverage. The
checked-in representative example is
`tests/fixtures/evaluation/phase4_quality_v1.json`.

## Metrics and reproducibility

Each profile records Recall@k, MRR, nDCG, filtered correctness, no-result behavior, refusal accuracy,
groundedness, explicit numbered-citation coverage, expected-token coverage, p50/p95 latency, and
reranker-unavailable counts. Runs compare:

- semantic baseline;
- hybrid BM25 + vector + RRF without reranking;
- lexical reranking;
- whole-chunk embedding reranking;
- max-sentence embedding reranking.

All profiles in a run use the captured corpus fingerprint, cases, queries, and filters. If the
indexed corpus changes after a run is queued, the job fails with `evaluation_corpus_changed` before
issuing a query; the operator must queue a fresh run. The last successful run on the same dataset is
the regression baseline. A drop larger than `APE_EVALUATION__MAXIMUM_METRIC_REGRESSION` is persisted
as a regression. Results remain attached to the run; no mutable global baseline table or separate
evaluation platform is introduced.

## Grounded answer behavior

Chat uses hybrid retrieval by default. `GroundingService` checks evidence before generation. No
results, a score below threshold, or low query/evidence token coverage produces a deterministic
insufficient-evidence answer and skips the LLM call. This outcome is persisted with a stable reason.

For generated answers, prompt `v2` requests numbered citations. The service splits answer segments,
validates cited or best-matching evidence, and persists:

- `content` — the compatible answer field;
- `citations` — existing durable source snapshots;
- `claims` — claim text, grounded flag, and source chunk/document/page/offset;
- `grounded` — whether every claim meets the configured support threshold;
- `insufficient_evidence_reason` — null for generated answers, stable reason for refusals.

The SSE `done` event carries the same citations, claims, grounded flag, and refusal reason.

## Reranker decision

Learned rerankers are not activated by a feature toggle alone. A candidate is eligible only when it
uses a learned embedding backend, improves nDCG by the configured minimum, does not reduce grounded
answer quality, stays within the p95 latency penalty, and has zero unavailable cases. The hash
embedding backend is marked non-learned and cannot be promoted.

No learned reranker is promoted by this change because the repository's reproducible local profile
uses hash embeddings; there is no honest learned-model gain or latency evidence to justify changing
the active lexical baseline. Deployments with a learned embedding provider can run the same stored
comparison and promote only a candidate whose persisted report passes every threshold.

## Configuration

- `APE_CHAT__MINIMUM_EVIDENCE_SCORE`
- `APE_CHAT__MINIMUM_QUERY_TOKEN_COVERAGE`
- `APE_CHAT__MINIMUM_CLAIM_TOKEN_COVERAGE`
- `APE_EVALUATION__DEFAULT_TOP_K`
- `APE_EVALUATION__MINIMUM_*` quality thresholds
- `APE_EVALUATION__MAXIMUM_P95_LATENCY_MS`
- `APE_EVALUATION__MAXIMUM_METRIC_REGRESSION`
- `APE_EVALUATION__RERANKER_CANDIDATES`

## Failure behavior and testing

Reranker provider errors preserve fused RRF order, mark diagnostics as `unavailable`, and count
against promotion. Corpus drift fails before evaluation, and LLM/provider failures fail the durable
evaluation job; both retain stable details through the existing runtime. Project scoping applies to
every dataset, run, job, query, and result.

Unit tests cover evidence refusal, explicit-citation behavior, claim locations, metrics, measured
hybrid/reranker gains under identical inputs, corpus drift, reranker fallback, and console rendering.
Integration tests cover migrations, immutable versions, durable submission, version/corpus capture,
and cross-Project isolation.

## Intentional non-goals

No agents, GraphRAG, fine-tuning, automatic configuration mutation, external evaluation service,
long-term metrics warehouse, corpus/index lifecycle changes, or customer-facing evaluation UI.
