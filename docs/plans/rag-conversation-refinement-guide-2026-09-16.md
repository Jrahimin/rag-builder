# Deployed conversation review and refinement guide

Review started 16 September 2026, after deployment of the latest fixes. Application code was not changed. Local checkout: `24b3057` (`conversation and evidence management improved`), clean at review start. The operator UI was reloaded before testing. The UI does not expose a deployed Git SHA, so deployment identity is user-reported; new citation metrics and `validated_partial_scope` were observed in production.

This guide supplements sections 3–7 and the section-9 checklist in `rag-run-investigation-2026-09-16.md`. Findings below distinguish live observations, direct local probes of the checked-out implementation, and remaining acceptance work. It evaluates source handling and conversation behavior, not independent legal correctness.

## 1. Production evidence

Project: Bangladesh Income Tax & Business Legal Guide (`f932c9e0-40af-4ff3-8704-ac3e542f2a8c`). Fresh conversation prefix: `44135a20`. Saved and current project revision: 10. Retrieval translation: off. Source metadata generation: 101. Observed providers: `gpt-5.6-luna`, Cohere `embed-v4.0` and `rerank-v4.0-pro`.

### Broad inactive-company question

Prompt: “A private limited company in Bangladesh has had no business activity this year. What annual company-law and tax obligations still apply? Explain the main duties and deadlines plainly, and distinguish anything the available sources do not establish. Cite the sources.”

Result: partial answer in Bangla despite an English question. It covered AGM, financial statements and company tax returns, but omitted annual-return timing, accounting-record duties and auditor appointment. It added a separate requirement to establish the full relevant Finance Act 2026 scope. The answer repeated inactivity qualifications and missing-evidence caveats, including a long final recap.

| Measurement | Observed result |
| --- | --- |
| Round trip | 192,265 ms |
| Coverage/recovery | 168,140 ms |
| Generation | 17,807 ms |
| Claim verification | 948 ms |
| LLM / rerank / embedding calls | 9 / 12 / 18 |
| Embedded texts | 376 |
| Structured retries | 3 |
| Total reported LLM input / output tokens | 199,490 / 15,906 |
| Supplied / cited passages | 5 / 5 |
| Claim inspector | 14 of 24 supported |
| Recovery outcome | `partial_answer`, `validated_partial_scope` |

This is improved availability compared with the previous schema-error refusal, not improved broad-answer completeness or demonstrated lower latency. Individual provider runs are not a controlled performance comparison.

Six missing-evidence bullets were incorrectly classified as `source_assertion` with “No valid citation supports this claim.” Their preamble was “উপলভ্য উদ্ধৃতিগুলো থেকে নিচের বিষয়গুলো নিশ্চিতভাবে বলা যায় না:”. Other failures included an uncited opening/conclusion and a nine-month assertion whose cited evidence did not establish the duration. Those latter failures must not simply be exempted as scope prose.

The final R1 check had section-36 evidence (`199e3a01-85ab-4664-bc5b-34e3cf5703f1`), but still said the filing deadline was missing and had `needs_adjacent_context=false`. Only VAT received an adjacent query. Five requirements, R1/R3/R4/R6/R7, were marked focused; only two focused queries actually ran: annual return and section-190 balance-sheet filing.

Authority reporting correctly distinguishes cited `not_assessed` from global `unresolved_relationships`. Records still include missing operative dates and empty provision scopes. No metadata was edited in this review.

### Focused English follow-up

Prompt: “For a private limited company in Bangladesh, what is the annual return filing duty and deadline? Answer in English, briefly, and cite the sources.”

The answer correctly returned to English and recovered the duty and 21-day deadline with two citations. Round trip: 58,171 ms; recovery: 44,001 ms; seven LLM calls, six reranks, 210 embedded texts and one structured retry. The inspector showed three of four claims supported.

The planner still created R3, “Official RJSC guidance or procedure confirming the annual return filing duty and deadline,” and partial coverage still listed that confirmation as missing. The generated caveat “The available sources do not establish a separate RJSC e-filing procedure, fee, or any different deadline” became an unsupported source assertion. Thus the latest prompt warning against unnecessary second-source confirmation has not reliably fixed production behavior. The caveat also expands the missing-confirmation topic into procedure, fees and alternative deadlines; accepting it wholesale would hide the cross-facet scope problem.

### Three-bullet Bangla rewrite

Prompt: “এটি তিনটি সংক্ষিপ্ত বাংলা বুলেটে বলুন। মূল দায়িত্ব, সময়সীমা এবং প্রযোজ্য সীমাবদ্ধতা রাখুন, সঙ্গে সূত্র দিন।”

The answer preserved annual-return duties and timing, returned exactly three Bangla bullets, and avoided the earlier procedural-gap refusal. Round trip: 14,351 ms; two LLM calls, one rerank, four embedding calls, 16 embedded texts; recovery `not_needed`. This is the clearest live improvement.

The inspector nevertheless showed three of four claims supported. The additional sentence about the certificate for members exceeding fifty was `unverified`, reason `derived_quantity`. Inspect the exact persisted citation and admitted span before changing numeric verification. Local number-parser probes already recognize both `পঞ্চাশ` and `পঞ্চাশের অধিক` as 50, so simply adding a spelling alias is not an evidence-based fix. The local source artifact contains the statutory wording, but that does not establish that the exact live cited excerpt contained it.

Only these three new live turns were run. The timing comparison with earlier runs is descriptive; configuration and source generation were observed, but repeated-run acceptance, broader numeric challenges and production tax calculations were not completed in this review.

## 2. Fix first: recovery must account for actual attempts

**Severity: high. Live failure plus code-level cause.**

In `services/evidence_repair_service.py`, all untried missing requirement IDs are added to `focused_requirement_ids` before the model returns its plan. The resulting query list is then truncated to two. `_requirement_progress` infers a route from that set and calls cited proof IDs `discovered_ids`. It cannot establish which requirement was actually searched. A requirement may be treated as having consumed its retry when no query addressed it.

Refine the existing plan response to bind each query to requirement IDs. Record scheduled, executed, failed and skipped attempts separately, and mark an attempt consumed only after dispatch. Preserve the global follow-up/query/time budgets; do not create one unbounded retry loop per requirement. If budget prevents an attempt, report `budget_exhausted_before_attempt` for that requirement instead of `focused`.

Allocate remaining budget to the user's core duties before optional procedures or whole-act audit tasks. Split duty, deadline, applicability and optional consequences sufficiently to preserve a proven duty when another facet is missing. Do not let an invented “prove the complete Finance Act scope” requirement consume the same priority as the requested annual filing duty. Changes to a review's requirement description should preserve explicit parent/facet identity rather than silently changing what `supported=true` means.

The focused live failure shows that another prose instruction is insufficient. Have each requirement identify its origin: explicit user request, necessary applicability condition evidenced in a source, or optional corroboration. Validate origin against the current request/evidence. Optional corroboration cannot prevent completion of an otherwise proven duty/deadline; actual source conflict or delegated procedural requirements still can. Avoid jurisdiction-specific string bans such as forbidding every RJSC requirement.

**Acceptance:** replay the broad trace with five missing requirements and only two query slots. Exactly the dispatched requirements acquire attempts. The annual-list duty survives partial selection; unattempted audit/minimum-tax/VAT work remains explicitly unattempted.

## 3. Allow structural recovery when an anchor is found late

**Severity: high. Live omission; scheduling limitation verified in code.**

Both continuation eligibility and adjacent-request construction currently depend on the first review round (`round_index == 0` / `not round_index`). A useful section anchor discovered by a focused search can arrive after adjacency is no longer eligible. The live annual-return anchor appeared late; no company-law adjacent query ran. The reviewer also did not request continuation, so changing the round guard alone will not guarantee recovery.

Track whether the particular requirement/anchor has had a structural attempt, within the existing total budget. Prefer its immediate same-section continuation when the admitted evidence visibly stops mid-rule or covers only part of the governing section. Use observed headings, offsets and neighboring structure as discovery signals, never as proof. Reserve or replace a remaining search slot; do not add unlimited extra rounds. Keep final source admission, authority and exact proof validation intact.

**Acceptance:** a fixture where the section-36 anchor first appears after focused retrieval must still recover the necessary companion passage or record an explicit budget stop. Assert both proof IDs survive final context selection. Do not hard-code section 36 or the expected 21-day value into production queries.

## 4. Fix scope verification in both directions

**Severity: high. Live false negatives and directly reproduced false positives.**

`grounding_service.py` misses ordinary Bangla scope wording such as `উপলভ্য ... নিশ্চিতভাবে বলা যায় না`. The current phrase list recognizes a few different spellings and constructions. Preserve the actual list preamble/heading through segmentation and classify each dependent bullet using that context. Distinguish “no final amount can be calculated from these inputs” from “no tax is payable”; do not exempt a whole sentence merely because it contains a limitation.

More seriously, `_coverage_topic_supported` accepts substring/40% token overlap or any shared bilingual concept. A direct read-only probe with validated missing topic **“Annual return electronic filing fees”** classified both **“The supplied sources do not establish the annual return deadline”** and **“…annual return duty”** as supported. Sharing “annual return” is insufficient: missing fees do not establish missing duty or timing.

Carry requirement/facet IDs into scope validation. Match the generated limitation to the specific unresolved facet and its qualifiers, not a broad noun overlap. Use a bounded classification step only for unresolved paraphrases, or generate concise scope descriptions directly from the validated missing-facet structure. Keep full-corpus absence unsupported. Maintain factual verification for legal conclusions joined to scope statements.

**Acceptance:** test the exact six live Bangla bullets with the production `knowledge_repair` envelope; preserve genuine uncited legal-conclusion failures. Add duty/deadline/fee cross-facet negative cases in both languages, mixed scope-plus-legal conclusions, and wrong whole-corpus absence claims. A higher supported percentage alone is not acceptance.

## 5. Replace brittle rewrite eligibility with explicit turn intent

**Severity: high for conversational continuity. Direct implementation probes.**

The current `rewrite_retrieval.py` keyword gate produces:

| Request | Current citation-recall eligibility | Required behavior |
| --- | --- | --- |
| Make it shorter. | No | Preserve topic and source context |
| Summarize it and also keep the citations. | No | Preserve topic; “also” is not new subject matter |
| Summarize it and include filing penalties. | Yes | Retrieve evidence for the added penalties |
| Rewrite it in a table. | Yes | Preserve the same factual scope |

Use the existing turn-resolution call to distinguish presentation-only, factual follow-up, correction and mixed requests. Add a small validated field identifying new factual facets; avoid another LLM call solely for routing. For mixed requests, retain prior validated context and search the additions. Do not restrict all retrieval to old citations.

Recall currently iterates all persisted citation snapshots, which include supplied-but-unused passages, and falls back only when the result is completely empty. Prefer evidence actually supporting retained claims, while preserving required companion passages. Revalidate against current build, project, filters and source generation. Nonempty but incomplete recall must not silently count as complete: if a necessary supporting passage disappears, recover its facet or narrow the answer explicitly. Never reuse stale historical excerpts as current evidence.

**Acceptance:** natural English/Bangla phrasing, formatting-only additions, factual additions without the word “add,” source removal/replacement, partial recall, and citation renumbering. A rewrite should not rerun a broad legal audit unless its factual scope changed.

## 6. Improve useful brevity and language consistency

The broad English prompt produced Bangla. In `prompt_builder.py`, the explicit original-user-language reminder is inside the `missing_inputs` branch, not a general output-language contract. Resolve requested language once from the original message and conversation preference; keep retrieval/source language separate. Explicit language requests should win over source language.

Lead with a short answer to the user's actual question. Present each main duty once with its timing and citation. Put all remaining uncertainty in one compact paragraph or small list. Avoid repeating the same caveat after every duty and again in the conclusion. Do not add penalty procedures, full-amendment inventories or unrelated liability calculations merely because retrieved text mentions them. A broad question can justify a structured answer without a long recital of every unavailable topic.

State material period assumptions plainly. For tax deadlines, explain a supported conditional date rule rather than saying “the special September rule, if applicable”; ask for the income-year end only if needed to calculate the user's exact date. This is an answer-design requirement, not permission to invent the missing condition.

**Acceptance:** English broad/focused prompts remain English; explicit Bangla rewrites remain Bangla; three-bullet requests return three bullets. Human review checks whether the answer leads with useful information and states each scope limitation once.

## 7. Reduce repeated review cost before raising limits

The live bottleneck is coverage/recovery, not final verification. The 300-second timeout prevented an early failure but did not make a 192-second answer a good experience. Three structured retries resubmitted large contexts. Record the exact validation reason for each retry; the current successful trace counts retries without making their individual causes readily inspectable.

First remove accidental requirements, duplicate review material and false consumed-attempt bookkeeping. Preserve validated proof with stable requirement identity; review missing facets against new evidence plus the minimum confirmed context needed for applicability. Keep exact source ranges and authority identity. Measure prompt tokens per phase and stage durations; do not assume available model context is a sensible per-turn budget.

Add a bounded embedding LRU only after measuring repeat hits, keyed by text hash, model/version, purpose and relevant configuration. The live trace already reports 180 embedding-cache hits; another cache will not eliminate expensive schema retries or LLM coverage reviews. Avoid a general agent framework, corpus-wide reindex, larger top-k, or indiscriminate threshold reductions as the first fix.

## 8. Verification, authority and UI still need acceptance work

- Semantic similarity still directly produces `supported` above a threshold in `_cross_language_verification`. Add bounded multilingual entailment for ambiguous factual claims, with subject, negation, condition, quantity and duration checks; use similarity to locate evidence. Preserve rejection of 11 versus 12 years and acceptance of exact 21-day/3-week equivalence. An unrelated passage with another duration must not be labelled a specific contradiction solely because embedding scoring is unavailable.
- The nine-month citation failure in the broad answer needs inspection of its exact cited span before deciding whether generation chose the wrong citation or span selection lost context. Do not solve it by increasing a score or exempting durations.
- Keep cited authority assessment separate from retrieval-expansion health. Audit missing effective dates/provision scopes against documentary sources; use immutable metadata revisions. No invented MODIFIES edges and no assumed legal currency from empty relationships.
- Display the new per-requirement attempt history as a compact table, not only huge raw JSON. Distinguish missing evidence, unsupported facts and unassessed authority. Keep supplied versus cited counts and all citation numbers accessible.
- Streaming currently switches to “Checking evidence” when any LLM call occurs, and has no finer review progress or elapsed-time feedback. Emit actual stage/attempt progress, show elapsed time, and offer cancellation. Do not label an in-progress answer “Grounded response” before verification. Inspect clean EOF without a terminal event: the stream client currently returns accumulated content without requiring completion, so interrupted streams need an explicit incomplete state.

## 9. Implementation order and release gates

1. Capture the live broad trace as redacted deterministic fixtures. Fix query-to-requirement accounting, late structural recovery and partial-proof retention together.
2. Fix Bangla list context and cross-facet scope false positives together. Do not ship only the more permissive phrase recognition.
3. Refine turn intent, partial source recall and language/length policy. Then rerun focused → rewrite → mixed-follow-up conversations.
4. Reduce review payload/retries using measured phase costs. Add targeted entailment after preserving deterministic numeric and authority guards.
5. Complete the metadata audit and disposable `ape_test` integration/`created_by` fixture work independently of prompt changes.

For each increment run relevant unit suites, architecture/evaluation smoke, frontend contract tests, typecheck/build and disposable integration where retrieval contracts change. Preserve the previous validation record rather than treating its passed unit suite as new production acceptance.

Before closing section 9 of the original plan, run at least three provider-backed repetitions per frozen broad/focused/rewrite case, recording source generation/build, project snapshot, models, full/partial coverage, factual and scope failures, query-to-requirement routes, latency and tokens. Include headerless tables, long sections, Bangla scope lists, equivalent and conflicting durations, unrelated evidence, incomplete recall and tax cases. Semantic entailment, embedding LRU, per-requirement event history, integration and the documentary metadata audit remain unfinished work, not implied successes of this deployment.

## 10. Implementation status — 17 September 2026

Implemented in the local branch:

- Search plans now bind each query to requirement IDs, retain requirement origin, record executed routes and distinguish budget-exhausted requirements that were never attempted. Reviewer-created requirement IDs no longer enlarge the user's requested scope.
- A focused result can trigger one bounded structural-adjacency recovery. The cited anchor and previously confirmed proof survive final context selection.
- Scope verification recognizes the observed Bangla limitation form and rejects duty/deadline/fee cross-facet matches. Similarity is used to locate candidate spans, with deterministic polarity, quantity and duration guards deciding the high-risk cases covered here.
- Turn resolution distinguishes presentation-only and fact-adding follow-ups. Citation recall is revalidated against the current corpus, incomplete recall is supplemented, and chained presentation requests retain the last factual topic.
- The generation contract now applies language consistency and concise-answer guidance to every turn.
- Structured-output retry diagnostics include the finish/schema reason without retaining source text.
- Test Lab exposes elapsed progress, a compact recovery-attempt history, explicit terminal-stream handling and operator cancellation. A cancelled stream is shown as cancelled and cannot be accepted as an answer.

Local provider replay passed a focused answer, an English brevity rewrite, a chained one-bullet Bangla rewrite with citations, and a mixed follow-up that retained prior proof while retrieving the added seller-delivery facet. Unit, type, lint and production-build checks pass. Production repetition remains a release gate because these branch changes have not been deployed.

Still separate work: a broader learned entailment layer, embedding LRU measurement/implementation, full scheduled/failed/skipped per-requirement event persistence, `ape_test` integration and its `created_by` fixture, the documentary authority metadata audit, and three provider-backed repetitions for every frozen acceptance case. These items are not represented as completed by this change set.
