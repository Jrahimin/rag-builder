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
`metadata_filter`, `multilingual`, `cross_lingual`, `code_switched`, `no_answer`, and `citation`.
Optional query/evidence language labels provide per-language-pair reporting but never change
runtime decisions. Answerable cases identify relevant
chunk or document IDs. Optional expected answer tokens measure practical answer coverage. The
checked-in representative example is
`tests/fixtures/evaluation/phase4_quality_v1.json`.

## Metrics and reproducibility

Each profile records Recall@k, MRR, nDCG, per-language-pair recall/nDCG, filtered correctness,
false-refusal rate, false-accept rate, groundedness, unverified-claim rate, explicit
numbered-citation coverage, expected-token coverage, p50/p95 latency, and reranker-unavailable
counts. Runs compare:

- semantic baseline;
- hybrid BM25 + vector + RRF without reranking;
- lexical reranking;
- whole-chunk embedding reranking;
- max-sentence embedding reranking;
- bounded-passage semantic evidence (raw cosine, independent of rerank order).

`semantic_score_calibration` records positive minimum/median, hard-negative
maximum/p95, and observed separation overall and per language pair. Thresholds
must be reviewed against these stored distributions after each model or corpus
baseline change; they are not derived from RRF or reranker scores. Acceptance
checks enforce the configured minimum cross-language recall and maximum
false-refusal/false-accept rates on every run.
`passage_semantic_score_calibration` stores the same distribution separately. Evaluation also
reports rank-1 accuracy and `accepted_without_relevant_evidence_rate`; rechunk-stable evidence
phrases prevent UUID churn from turning a bad retrieval into a false pass.

The checked-in provider benchmark for the 1024-dimension `text-embedding-3-large`
baseline measured cross-language Recall@1 of `1.0` across en↔bn and en↔fr, with
minimum relevant similarity `0.387` and maximum sampled hard-negative similarity
`0.343`. That distribution supports the initial `0.35` semantic gate. The lexical
rescue floor is `0.30`: same-language OCR/table hits can land just under `0.35`
while still sharing most query tokens, and cross-language near-misses typically
have ~0 coverage so they still refuse. Operators may raise the floor after
measuring their own positive vs hard-negative margin. These figures are not a
universal constant; production datasets must re-establish the separation before
certifying the threshold.

All profiles in a run use the captured corpus fingerprint, cases, queries, and filters. If the
indexed corpus changes after a run is queued, the job fails with `evaluation_corpus_changed` before
issuing a query; the operator must queue a fresh run. The last successful run on the same dataset is
the regression baseline. A drop larger than `APE_EVALUATION__MAXIMUM_METRIC_REGRESSION` is persisted
as a regression. Results remain attached to the run; no mutable global baseline table or separate
evaluation platform is introduced.

## Grounded answer behavior

Chat uses hybrid retrieval by default. Ranking `score` (RRF or reranker output) and calibrated
`semantic_score` are separate. `GroundingService` evaluates one candidate/evidence unit at a time:
direct semantic acceptance and any lexical rescue must come from the same chunk (or the same winning
passage span). Lexical query coverage is rescue-only: it can admit a semantically plausible
candidate above a lower semantic floor, but can never reject a strong semantic candidate. No
language or script detector is part of this decision. Stored Project `evidence_score_threshold`
values below `0.15` are leftover
RRF-scale overrides and are ignored; leftover `minimum_query_token_coverage` values below the
deployment rescue coverage are ignored so they cannot loosen false-accept protection.

For generated answers, prompt `v4` requests a concise answer in the question's language, forbids
unsupported background or uncited data rows, and requires numbered citations even when evidence is
in another language. The service splits answer segments, ignores Markdown-only scaffolding,
validates cited or best-matching evidence, and persists:

- `content` — the compatible answer field;
- `citations` — existing durable source snapshots;
- `claims` — claim text, compatibility grounded flag, three-state verification, and source location;
- `grounded` — whether no claim is `unsupported`;
- `best_semantic_evidence_score` — calibrated cosine of the best selected chunk;
- `best_evidence_score`, `evidence_score_method`, winning chunk, and span — the actual gate input;
- `insufficient_evidence_reason` — null for generated answers, stable reason for refusals.

Historical messages may still carry `evidence_best_score`, which mixed ranking and cosine
scales; new messages no longer write that key.

Claim verification is `supported` when lexical evidence clears the configured threshold,
`unverified` when a structurally valid citation exists but there is no shared vocabulary for the
lexical validator to assess, and `unsupported` otherwise. `unverified` is not semantic entailment;
its rate is reported explicitly and cross-language false accepts remain a hard regression gate.

The SSE `done` event carries the same citations, claims, grounded flag, and refusal reason.

## Reranker decision

Learned rerankers are not activated by a feature toggle alone. A candidate is eligible only when it
uses a learned embedding backend, improves nDCG by the configured minimum, does not reduce grounded
answer quality, stays within the p95 latency penalty, and has zero unavailable cases. The hash
embedding backend is marked non-learned and cannot be promoted.

No learned reranker is promoted by this change. The production baseline keeps fused RRF order
through the enabled rerank stage with a `noop` occupant (`rerank_status=passthrough`); the lexical
heuristic remains an
offline/provider-free comparison but is not language-general.
A true multilingual cross-encoder can be promoted only after its persisted report passes every
threshold.

## Configuration

- `APE_CHAT__MINIMUM_SEMANTIC_EVIDENCE_SCORE`
- `APE_CHAT__EVIDENCE_SCORE_MODE` (`whole_chunk` by default; `passage_max` only after calibration)
- `APE_CHAT__LEXICAL_CORROBORATION_FLOOR_SCORE`
- `APE_CHAT__LEXICAL_CORROBORATION_COVERAGE`
- `APE_CHAT__MINIMUM_CLAIM_TOKEN_COVERAGE`
- `APE_EVALUATION__DEFAULT_TOP_K`
- `APE_EVALUATION__MINIMUM_*` quality thresholds
- `APE_EVALUATION__MAXIMUM_FALSE_REFUSAL_RATE`
- `APE_EVALUATION__MAXIMUM_FALSE_ACCEPT_RATE`
- `APE_EVALUATION__MAXIMUM_ACCEPTED_WITHOUT_RELEVANT_EVIDENCE_RATE`
- `APE_EVALUATION__MAXIMUM_P95_LATENCY_MS`
- `APE_EVALUATION__MAXIMUM_METRIC_REGRESSION`
- `APE_EVALUATION__RERANKER_CANDIDATES`

## Failure behavior and testing

Reranker provider errors preserve fused RRF order, mark diagnostics as `unavailable`, and count
against promotion. Corpus drift fails before evaluation, and LLM/provider failures fail the durable
evaluation job; both retain stable details through the existing runtime. Project scoping applies to
every dataset, run, job, query, and result.

Unit tests cover semantic-only rejection authority, lexical rescue, three-state claim verification,
explicit citations, metrics, measured hybrid/reranker gains under identical inputs, corpus drift,
reranker fallback, and console rendering. The provider-backed cross-language benchmark is opt-in;
hash embeddings cannot establish cross-language quality.
Integration tests cover migrations, immutable versions, durable submission, version/corpus capture,
and cross-Project isolation.

## OCR gazette calibration result (2026-08-18)

The versioned `ocr-gazette-grounding` dataset was run after reprocessing and activating an
immutable five-chunk build. The target page-three table is now one typed table chunk; the former
three-character fragment no longer exists. The active hybrid profile measured:

- Recall@5 `1.0`, rank-1 accuracy `0.4`, and MRR `0.633`.
- False-accept rate `0.0` and accepted-without-relevant-evidence rate `0.0`.
- Groundedness `1.0` and citation coverage `0.897`.
- Bangla exact-table retrieval ranked the table first. The Bangla synonym remained answerable but
  did not rank the table first.
- Both English table paraphrases remained safely refused: relevant whole-chunk cosine was about
  `0.13`/`0.18`, below wrong-chunk scores of about `0.20`/`0.28`.
- The section-106 numeral case ranked its relevant chunk first (`0.374`), but its strongest hard
  negative was close (`0.371`), so numerals are not evidence of general cross-lingual quality.

Bounded-passage scoring was not promoted: its positive/hard-negative margin was negative
(`-0.332`) and its measured p95 latency was about `3.9 s`. A 3072-dimension build of the configured
embedding model was also tested without indexing and reduced rather than improved separation.
`whole_chunk` therefore remains the evidence mode and passage scoring remains disabled by default.
The unresolved English-to-Bangla case requires a measured stronger multilingual embedding or
cross-encoder build; lowering the evidence threshold would increase false accepts and is not an
accepted workaround.

Raw Unicode query-token coverage remains the lexical rescue signal. A corpus-IDF-weighted
alternative was compared on the Bangla table query versus the section-106 near-negative; both
methods produced the same admit/refuse decision, so the simpler raw coverage was kept.

### Operator rollout after structure-preserving OCR

1. Reprocess OCR documents with the same `ocr_lang` so they receive parser 2.0.0 elements and
   chunker 3.0.0 table/tiny-fragment behavior.
2. Confirm the document is `ready`, table chunks keep page provenance, and no sub-minimum junk
   chunks remain.
3. If the job did not auto-activate, activate the validated immutable build (previous build stays
   retained for rollback).
4. Run the versioned `ocr-gazette-grounding` dataset against that corpus fingerprint. Do not lower
   `APE_CHAT__MINIMUM_SEMANTIC_EVIDENCE_SCORE` when English paraphrases still miss.
5. Create a new conversation or refresh the conversation configuration snapshot before verifying
   chat. Historical snapshots keep the evidence mode and thresholds captured at creation.

## Intentional non-goals

No agents, GraphRAG, fine-tuning, automatic configuration mutation, external evaluation service,
long-term metrics warehouse, corpus/index lifecycle changes, or customer-facing evaluation UI.
