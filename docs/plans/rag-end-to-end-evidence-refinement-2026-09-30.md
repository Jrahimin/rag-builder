# End-to-end RAG correctness, source fidelity, and latency refinement

## 1. Context, evidence, and decisions

The next implementation should establish one consistent contract for **what the user asked, what evidence proves it, and what remains unresolved** across retrieval, recovery, generation, verification, and display.

The investigation found defects upstream of answer generation. Increasing timeouts or adjusting prompts alone would leave those defects intact.

### Reviewed baseline

- Repository: `E:\python-projects\rag-builder`
- Git HEAD: `dbee1df7003f479343ecfb2c44f114e5c92407db`
- Tracked working-tree diff SHA-256: `0af5e8315385a80f254c441ce60b5159afec8736f75b8251b08f31969581f7e0`
- The working tree contains earlier implementation and repair work, including untracked `quantities.py`. Preserve and integrate it.
- Project: `2ee2756f-ad27-44df-a9d3-1316b10ccbb1`
- Saved QA: configuration revision 45, source metadata generation 35, index build `8eb1595a-8e38-41e9-96c5-0ba419e79bee`.

Evidence includes the [six-response manifest](E:/python-projects/rag-builder/browser-qa-responses/manifest.json), [combined API responses](E:/python-projects/rag-builder/browser-qa-responses/regular-messages-api-responses.json), the earlier [manual response export](C:/Users/user/.codex/attachments/db083c9a-c26c-4327-b6c9-926ea8ad4ab2/Pasted%20text.txt), current code, and read-only database/source-text checks.

**Evidence distinction:** the six saved responses are regular **GET message-list responses containing persisted messages**. They are not newly captured regular POST generation responses. The manual export contains earlier POST-style response envelopes.

This investigation changed no application code or source metadata. Thirteen focused tests passed. Those tests do not cover all the reproduced failures below. No fresh answer-generation benchmark was run.

### Decisions established with the user

- Complex requests may take **up to 60 seconds** when additional work can materially improve the answer.
- Simple lookups should retain a fast path.
- Verified official guidance may support an **explicitly attributed answer**. A claim that something is the operative law requires governing applicability evidence.
- Keep the platform domain-agnostic. Bangladesh tax terminology and expectations belong in project configuration and evaluation fixtures.

## 2. Findings and their implications

### Observed outcomes

Times below use the saved assistant message’s `total_latency_ms`.

| Case | Outcome | Server time | Investigation result |
|---|---|---:|---|
| Explicit AY 2026–27 threshold | Timeout/refusal | 35.642 s | Additional evidence was retrieved, but the final review ran out of time. |
| Historical AY 2025–26 threshold | Refusal | 22.216 s | The query retained its wording, but retrieval used no historical `as_of`; reviewed evidence concerned later periods. |
| Current/yearless threshold | Answered | 35.273 s | The full stored passage contains the requested period/category heading and Tk 400,000 row. |
| Companies Act AGM | Answered | 10.638 s | A useful control case that avoided recovery. Preserve this behavior. |
| DNCC application documents | Refusal | 22.525 s | The requested checklist was found; a literal English qualifier guard incorrectly downgraded the Bengali evidence. |
| Office-rent withholding | Partial answer | 30.206 s | Relevant operative passages exist in the corpus but do not appear in the saved recovery evidence. |

These are corpus-relative findings. The stored tax values have not been independently certified against the original official publications.

### F1 — Keyword preselection discards relevant evidence before BM25

**Confirmed and reproduced.**

In [chunk_keyword_index_repository.py](E:/python-projects/rag-builder/backend/app/modules/retrieval/repositories/chunk_keyword_index_repository.py:113), candidate matching accepts either:

- PostgreSQL full-text matches; or
- overlap with the application tokenizer’s terms.

However, candidates are ordered by the full-text score and limited **before** Python BM25 scoring.

For the saved rent questions:

| Query | Matching candidates | Candidates with nonzero preselection score |
|---|---:|---:|
| Original English question | 2,276 | 0 |
| Bengali recovery query | 1,812 | 0 |

Every candidate therefore ties on score and is ordered by UUID. The relevant numbered Section 109 passage is at position **1,588** for the Bengali query—outside the configured candidate cap.

**Implication:** BM25 and reranking cannot rescue passages already excluded. This is a major retrieval defect, not an answer-model problem.

### F2 — Temporal interpretation is incomplete before retrieval

**Confirmed in the saved first-turn path.**

[chat_service.py](E:/python-projects/rag-builder/backend/app/modules/conversations/services/chat_service.py:800) bypasses conversation resolution when there is no usable history. [turn_resolver.py](E:/python-projects/rag-builder/backend/app/modules/conversations/turn_resolver.py:339) then constructs a standalone resolution without interpreting the requested period.

Consequently, the explicit historical question records:

- `temporal_intent.kind = none`
- `retrieval_as_of = null`
- a current retrieval reference date.

The later evidence planner understands the requested assessment year, but it receives candidates already retrieved under a different temporal scope.

**Implication:** preserving the year in question text is necessary but insufficient.

### F3 — Historical source selection can resurrect outdated administrative metadata

**Confirmed by read-only reproduction.**

[source_metadata_read.py](E:/python-projects/rag-builder/backend/app/modules/knowledge/source_metadata_read.py:335) filters metadata revisions by effective interval before choosing the applicable activation.

For a historical lookup, the current date-qualified metadata can be excluded while an older revision of the **same document**, with null dates, becomes eligible. This occurred for the circular and Finance Act metadata inspected here.

**Implication:** administrative corrections and historical legal editions are being conflated. Simply adding `as_of` to more requests would expose this weakness more often.

### F4 — Literal qualifier checks cause false refusals

**Confirmed and reproduced.**

[evidence_repair_service.py](E:/python-projects/rag-builder/backend/app/modules/conversations/services/evidence_repair_service.py:438) requires certain English words to appear in selected quotations.

For DNCC, the requirement contains “conditional.” The source expresses conditions through tenant and factory clauses, but lacks that English word. The guard adds:

`requested category or window conditional`

This happens even with an English paraphrase that clearly expresses the conditions.

**Implication:** a guard intended to prevent overly broad claims rejects valid semantic equivalents and cross-language evidence. The resulting `unresolved_authority` response misidentifies the failure.

### F5 — Structural recovery exists now, but arrives late

**Confirmed; this updates the older diagnosis.**

The earlier blanket statement that focused recovery cannot perform adjacency is no longer accurate. Current code allows a separate structural round, and the explicit threshold trace records one.

The problem is now ordering and cost:

- Initial retrieval: 5.375 s.
- Recovery planning: 10.002 s.
- Coverage reviews: 11.821 s combined, including the interrupted review.
- Recovery deadline: 30 s.

These timings overlap with broader stages and must not be added indiscriminately.

**Implication:** structural completion should usually precede the expensive review, with later reviews restricted to changed evidence.

### F6 — Scope fields are present but not populated end to end

**Confirmed code gap.**

[ports.py](E:/python-projects/rag-builder/backend/app/modules/conversations/ports.py:131) defines `EvidenceScope`, including applicable period, provisions, and amendment dependencies. Current application searches found readers for several fields but no ingestion producers.

Likewise, authority logic checks `provision_effect` and `replacement_scope_verified`, but the relationship storage and retrieval record do not provide a complete production path for those facts.

**Implication:** adding fields and conservative guards has not completed the information flow needed to resolve those guards.

### F7 — Citation support loses important parts of the proof

**Confirmed representation gap; broader correctness risk.**

The successful threshold response’s full chunk contains the period, taxpayer category, schedule reference, and amount. Its persisted claim evidence contains only the numeric table row.

The row’s saved character offsets match the parsed source exactly. Therefore this is **not an offset corruption finding** for that example.

However, [grounding_service.py](E:/python-projects/rag-builder/backend/app/modules/conversations/grounding_service.py:1247) can classify support using lexical coverage or embedding similarity. Those signals do not independently prove period, category, conditions, or amendment effect.

**Implication:** retain the complete proof across coverage review, generation, and citation display; do not reconstruct it from the shortest similar span.

### F8 — Parsing quality and source usability remain separate concerns

**Observed limitation.**

The DNCC extraction contains damaged words despite a high overall parse-quality score. Its semantic chunks have no precise character offsets. The threshold table carries a long preceding context that includes unrelated front matter.

**Implication:** whole-document quality scores are insufficient for evidence-critical rows and clauses. Improve structural boundaries and provenance without silently “correcting” substantive OCR text.

### F9 — Recovery sometimes answers a prerequisite instead of the requested result

**Confirmed in the rent case.**

Recovery spent effort defining “specified person,” while the requested duty and rate remained unresolved. The corpus contains:

- Section 109: chunk `e20e01a7-06a7-4f37-8230-effe85d761e4`.
- The circular’s rent-withholding table: chunk `8e43726f-b200-4402-b072-386c27276c15`.

Neither appears in the saved recovery evidence.

**Implication:** discovery priority should favor the central requested proposition and its necessary dependencies. A definition-only response must not count as successful completion of a rate question.

### F10 — Polling and repeated reads are secondary overhead

Current Test Lab code already disables recurring jobs/index-build polling on the Messages tab. On relevant operational tabs:

- Jobs poll every 3 seconds while active, otherwise 15 seconds.
- Index builds poll every 5 seconds.
- Streaming completion fetches messages, then invalidation can fetch them again.

These requests support operational status and UI refresh. They do not explain the demonstrated retrieval failures, but idle polling and duplicate completion reads can be reduced.

## 3. Target design and research basis

Use this bounded flow:

```text
Normalize request scope
    → Retrieve relevant candidates under that scope
    → Complete source structures and resolve applicable dependencies
    → Review a compact evidence bundle
    → Recover only specific missing evidence, within a shared budget
    → Generate from approved facts and proof references
    → Verify scope, quantities, citations, and unsupported additions
    → Persist and display the same final result
```

Extend the existing `EvidenceUnit`, `EvidenceScope`, coverage verdict, proof map, source generations, and request telemetry. Avoid a parallel evidence engine.

The research supports selective changes:

| Research | Application here |
|---|---|
| [Contextual Retrieval](https://www.anthropic.com/engineering/contextual-retrieval) | Include source-grounded headings and local context in retrieval representations. Preserve original text separately as evidence. |
| [TableRAG](https://arxiv.org/abs/2410.04739) | Retrieve table structure and relevant rows together instead of treating isolated numeric rows as complete rules. |
| [Adaptive-RAG](https://arxiv.org/abs/2403.14403) | Select bounded processing depth by task complexity and observed evidence gaps. |
| [ALCE citation evaluation](https://arxiv.org/abs/2305.14627) | Evaluate correctness and citation completeness separately from answer fluency and citation presence. |
| [HoH temporal benchmark](https://aclanthology.org/2025.acl-long.301/) | Test current and outdated evidence together; merely adding newer documents does not establish temporal reliability. |

These are design principles, not evidence that their published performance gains will transfer to this repository. A new graph database, unrestricted agent loop, or universal model upgrade is unnecessary for the demonstrated defects.

## 4. Implementation guide

### A. Establish a reproducible baseline and contract

First save this plan and its evidence ledger as:

`E:\python-projects\rag-builder\docs\plans\rag-end-to-end-evidence-refinement-2026-09-30.md`

Preserve the six responses and manual examples as immutable inputs. Record code fingerprint, configuration, model identities, index build, source generation, and reference time with every replay.

Introduce one internal request-scope contract containing:

- Task kind: lookup, explanation, eligibility, calculation, comparison, or overview.
- Subject/category, jurisdiction, named source/instrument, and source restrictions.
- Requested period, period kind, exact `as_of` when supplied, and current-reference time.
- Explicit user inputs and separately identified project defaults.

Extend existing requirement/proof structures so every proposed factual conclusion references its necessary applicability evidence. Keep evidence validity, requirement completion, and authority assessment as separate states.

**Exit criterion:** all pipeline stages consume the same normalized scope and requirement identities.

### B. Correct lexical retrieval before changing ranking thresholds

Primary files:

- [chunk_keyword_index_repository.py](E:/python-projects/rag-builder/backend/app/modules/retrieval/repositories/chunk_keyword_index_repository.py)
- [keyword_retriever.py](E:/python-projects/rag-builder/backend/app/modules/retrieval/retrievers/keyword_retriever.py)
- [fts.py](E:/python-projects/rag-builder/backend/app/modules/retrieval/keyword/fts.py)
- [bm25.py](E:/python-projects/rag-builder/backend/app/modules/retrieval/keyword/bm25.py)

Changes:

1. Compute tokenizer-compatible BM25 relevance **before the candidate limit**, using PostgreSQL and existing build-scoped term/collection statistics.
2. Keep the Python BM25 implementation as the numerical test oracle; remove the production two-stage ordering that scores only an arbitrarily truncated subset.
3. Apply project, index, source-policy, language, and explicit document restrictions before ranking.
4. Use UUID only as the final tie-break between equal relevance scores.
5. Add the required JSONB term-frequency index through Alembic; verify the real query plan and migration drift.
6. Keep dense retrieval, RRF, and reranking. Do not compensate by admitting thousands of passages into generation.

Tests must include long queries where no document matches every term, mixed Bengali/English queries, and random UUID reassignment.

**Exit criterion:** known relevant passages survive lexical candidate selection independently of UUID order, with measured database cost.

### C. Resolve temporal and source scope before recall

Primary files:

- [turn_resolution.py](E:/python-projects/rag-builder/backend/app/modules/conversations/turn_resolution.py)
- [turn_resolver.py](E:/python-projects/rag-builder/backend/app/modules/conversations/turn_resolver.py)
- [chat_service.py](E:/python-projects/rag-builder/backend/app/modules/conversations/services/chat_service.py)
- [source_metadata_read.py](E:/python-projects/rag-builder/backend/app/modules/knowledge/source_metadata_read.py)

Changes:

1. Run scope extraction on every factual turn, including standalone turns without history.
2. Preserve existing explicit-period rewrite safeguards.
3. Represent assessment/fiscal periods separately from a timestamp. Do not silently translate “AY 2025–26” into an arbitrary date.
4. For current questions, resolve applicability from source-declared periods and project policy; publication recency is insufficient.
5. Select the latest administrative metadata correction within the pinned source generation **before** applying legal validity filters to the same content edition.
6. Retrieve historical content editions and passage-level periods explicitly. Do not recover old legal meaning by reviving obsolete administrative metadata.
7. Distinguish “rule applicable then” from “information published or known then,” especially for retrospective amendments.
8. Treat “in this corpus” as a corpus restriction. Distinguish a named instrument from an explicit “only this document” restriction.

Add regressions to [test_source_relationships.py](E:/python-projects/rag-builder/tests/integration/test_source_relationships.py) for null-date metadata followed by a date correction, historical editions, retrospective changes, and pinned generation replay.

**Exit criterion:** first-turn, follow-up, historical, and current variants preserve the same intended scope throughout retrieval and recovery.

### D. Complete structural evidence and authority data

Primary files:

- [structure_helpers.py](E:/python-projects/rag-builder/backend/app/modules/knowledge/services/chunking/chunk_strategies/structure_helpers.py)
- [adjacent_selection.py](E:/python-projects/rag-builder/backend/app/modules/retrieval/adjacent_selection.py)
- [source_metadata.py](E:/python-projects/rag-builder/backend/app/models/source_metadata.py)
- [current_authority.py](E:/python-projects/rag-builder/backend/app/modules/conversations/current_authority.py)
- [ports.py](E:/python-projects/rag-builder/backend/app/modules/conversations/ports.py)

Changes:

1. Build versioned structural units that connect headings, table headers, rows, continuations, material exceptions, and source spans.
2. Replace indiscriminate preceding-context accumulation with structural boundaries. Separate repeated headers, footers, and contents pages from operative context.
3. Populate `EvidenceScope` from source-backed facts. Each extracted period, category, or provision must identify its supporting source span.
4. Keep inferred extraction candidates distinct from validated facts. A filename or generated summary cannot establish applicability.
5. Extend modification relationships with typed effect, provision scope, supporting spans, and verification status. Carry those fields through the Knowledge read contract into retrieval.
6. Scope unresolved amendments to the claims they may affect. Preserve unrelated proven claims.
7. Keep complete-edition replacement distinct from partial amendment. The inspected SRO replacement relationship must not be reversed merely because its filename says “Amendment”; its operative text requires review.
8. For missing structural context, fetch bounded related units before semantic re-search. Preserve document/version and source-policy boundaries.

Retain raw extraction and exact evidence offsets. Where semantic/OCR transformations lack a reliable offset map, expose chunk-level provenance rather than fabricated page highlights.

Reprocess affected documents into a **new private index build**, validate it, and retain the previous build for rollback.

**Exit criterion:** the current threshold’s heading and row form one traceable evidence bundle, and future-year tables cannot silently inherit its scope.

### E. Replace phrase-based coverage decisions with typed proof checks

Primary files:

- [evidence_repair_service.py](E:/python-projects/rag-builder/backend/app/modules/conversations/services/evidence_repair_service.py)
- [evidence_coverage.py](E:/python-projects/rag-builder/backend/app/modules/conversations/services/evidence_coverage.py)
- [authoritative_compatibility.py](E:/python-projects/rag-builder/backend/app/modules/conversations/prompts/authoritative_compatibility.py)

Changes:

1. Remove literal-English qualifier matching as a semantic completion test.
2. Represent conditions as structured facets tied to quotations: who, when, what action, and under which condition.
3. Keep deterministic checks for exact periods, source identity, ranges, and quantities. Use the existing semantic review to interpret equivalent language.
4. Distinguish describing a conditional rule from proving that a particular person satisfies it.
5. Prioritize discovery of the requested rule/rate/duty. Retrieve definitions only when they are genuinely necessary.
6. Preserve instrument, jurisdiction, category, and period constraints in every recovery query.
7. Allow source-attested provision references to guide retrieval. Treat contents pages and cross-references as navigation, not final proof.
8. Rename proof-map diagnostics so “valid evidence retained” cannot be mistaken for “requirement fully satisfied.”
9. Return specific outcomes: missing source, missing input, unresolved amendment, incomplete structure, contradictory evidence, exhausted budget, or failed verification.

For DNCC, the intended result is a source-attributed checklist with its tenant/factory conditions, subject to OCR limitations. It must not require the user to prove their business type merely to describe those conditions.

**Exit criterion:** the DNCC reproduction passes without weakening negative tests for genuinely missing conditions.

### F. Make recovery finite, incremental, and budget-aware

Primary files:

- [evidence_repair_service.py](E:/python-projects/rag-builder/backend/app/modules/conversations/services/evidence_repair_service.py)
- [chat_service.py](E:/python-projects/rag-builder/backend/app/modules/conversations/services/chat_service.py)
- [request_work.py](E:/python-projects/rag-builder/backend/app/platform/providers/request_work.py)
- [project_ai.py](E:/python-projects/rag-builder/backend/app/platform/config/project_ai.py)

Extract recovery scheduling from the large repair function while retaining the existing proof map and validation logic.

Use these execution boundaries:

- One initial hybrid retrieval, with at most one useful source-language variant.
- One compact initial planning/coverage exchange.
- At most three targeted recovery queries across unresolved requirements.
- At most two structural-completion batches, bounded by existing anchor/document safeguards.
- At most one delta review after recovery.
- At most one schema/selector correction for a malformed review.

Every recovery action must identify:

- The unresolved requirement it targets.
- The evidence or scope change expected.
- Its remaining budget.
- Its terminal result.

Stop when the same scope and evidence fingerprint recur without progress. Preserve validated independent proof when another branch fails. Do not retry equivalent wording against the same passages.

Use one monotonic end-to-end budget, including verification and persistence. Reserve the final 10 seconds of the complex-request budget for completion or a deterministic, specific partial response.

Targets:

- Simple verified lookups: p50 ≤10 s, p95 ≤15 s.
- Complex requests: p95 ≤60 s.
- Sixty seconds is a ceiling, not a minimum effort allocation.

Reduce duplicate context and provider calls before adding persistent caches. Replace the embedding cache’s provider-wide lock with bounded per-key request coalescing only alongside cancellation, failure, and duplicate-request tests. Preserve project/provider/model/purpose isolation.

**Exit criterion:** each trace explains why recovery continued or stopped, and no request can enter an unbounded search/review cycle.

### G. Generate answers from complete proofs and preserve them in citations

Primary files:

- [grounding_service.py](E:/python-projects/rag-builder/backend/app/modules/conversations/grounding_service.py)
- [quantities.py](E:/python-projects/rag-builder/backend/app/modules/conversations/quantities.py)
- [prompt_builder.py](E:/python-projects/rag-builder/backend/app/modules/conversations/prompt_builder.py)
- [message.py](E:/python-projects/rag-builder/backend/app/modules/conversations/schemas/message.py)

Changes:

1. Generate an internal structured answer draft whose factual segments reference approved requirement and evidence IDs.
2. Render citations from those references instead of relying primarily on Markdown reparsing and citation inheritance.
3. Preserve multiple supporting spans where a claim depends on a heading, row, exception, or amendment.
4. Reuse reviewed proof for faithful restatements. Batch semantic checking only for new or changed assertions.
5. Use embedding similarity for candidate alignment, not as sufficient proof of entailment.
6. Retain the shared typed quantity parser and Decimal arithmetic. Verify quantity roles together with category and period.
7. Keep authority status separate from textual support. Explicitly distinguish an attributed circular explanation from a verified operative-law conclusion.
8. For partial responses, lead with the status of the user’s central question. Avoid presenting a prerequisite definition as the main answer.

For governed answers, buffer factual output until verification completes while continuing progress events. Regular POST, SSE final events, and subsequent GET responses must expose the same final content and proof.

**Exit criterion:** every displayed material claim has sufficient visible support for its amount, period, category, and stated authority.

### H. Align API diagnostics, UI behavior, and operational polling

Primary files:

- [operatorConsoleQueries.ts](E:/python-projects/rag-builder/frontend/src/api/operatorConsoleQueries.ts)
- [TestLab.tsx](E:/python-projects/rag-builder/frontend/src/features/lab/TestLab.tsx)
- [MessageInspector.tsx](E:/python-projects/rag-builder/frontend/src/features/lab/MessageInspector.tsx)

Add backward-compatible diagnostics for normalized scope, answer basis, requirement outcome, evidence selection/loss, and terminal stop reason. Preserve existing message envelopes.

In the inspector:

- Show claim-supporting excerpts first, with access to headings and surrounding evidence.
- Keep retrieved, supplied, and actually cited passage counts distinct.
- Explain partial answers and source limitations in plain language.
- Provide an authenticated export of request/response and trace data without credentials.

Use the final mutation response to populate message cache. For SSE compatibility paths that require a GET, perform one reconciliation fetch and reuse it.

Retain tab-aware polling. Stop or slow idle build polling, and refresh operational state after relevant mutations. This remains secondary to retrieval and evidence correctness.

Regenerate OpenAPI types and update the matching conversation, retrieval, source-governance, and operator documentation.

## 5. Validation, delivery order, and handoff

### Regression matrix

| Area | Required cases |
|---|---|
| Current versus historical | Yearless/current, explicit AY 2026–27, explicit AY 2025–26, future schedule, retrospective change, first-turn and follow-up variants. |
| Subject/category | General individual, special categories, corporate/mobile-operator rebate distractor, explicit user correction, unknown category. |
| Task scope | General rule, conditional checklist, personal eligibility, calculation, comparison, multi-topic partial answer. |
| Structure | Heading separated from row, multi-page table, repeated footer, missing row, OCR ambiguity, synthetic page numbers. |
| Business sources | DNCC versus DSCC; new application versus renewal; AGM first/subsequent meeting; statutory duty versus prerequisite definition. |
| Authority | Supporting circular, proposal, operative statute, scoped amendment, unknown amendment scope, complete replacement and savings clauses. |
| Verification | Bengali/Western digits, lakh/crore, year-shaped money, rate versus amount, negation, wrong year with identical amount, missing citations. |
| Reliability | Provider timeout, cancellation, malformed review, repeated query, unchanged evidence, source/index change, project isolation. |
| Transport/UI | Regular POST, SSE final, persisted GET, citation display, refresh behavior, polling while active/idle. |

### Acceptance criteria

1. **Retrieval:** the known threshold and rent passages survive candidate selection; changing UUIDs does not change relevance ordering.
2. **Temporal fidelity:** explicit historical scope is never replaced by current scope; obsolete administrative metadata is not revived.
3. **Correctness:** zero unsupported confident answers in the curated negative cases.
4. **Answerability:** all verified answerable seed cases succeed consistently across three controlled repetitions. Truly absent historical evidence produces a precise limitation, not an invented answer.
5. **Citations:** every material factual claim has valid source references covering its required scope; a numeric row alone cannot certify an omitted period.
6. **Recovery:** every request stays within its action limits and shared deadline.
7. **Latency:** report p50/p95 over sufficient repeated observations, separating warm/cold behavior and concurrency. Do not claim percentile success from six single runs.
8. **Parity:** POST, SSE final, GET, and UI agree on final answer, coverage, and evidence.

### Delivery order

1. Freeze evidence and add behavioral reproductions for keyword preselection, historical metadata selection, and DNCC fulfillment.
2. Fix lexical retrieval and pre-retrieval scope.
3. Correct metadata-history semantics and connect structural/authority evidence producers.
4. Refactor coverage and recovery around the shared contract.
5. Carry proof through generation, verification, and citations.
6. Complete UI/API diagnostics and remove redundant reads.
7. Replay the full curated matrix against the new index build before activation.

Use focused tests at each stage. Run the repository release gate separately when preparing the completed change for release; do not repeatedly run broad suites during diagnosis.

### Fresh-chat handoff

The saved implementation guide must contain:

- This baseline and its dirty-tree warning.
- Exact questions, response-file references, conversation IDs, source/chunk IDs, and observed outcomes.
- Confirmed findings separately from hypotheses and older issues awaiting replay.
- Schema/configuration changes, migration instructions, index rebuild/rollback procedure, and acceptance results.
- A clear record of which layer each patch changes and which paired positive/negative cases protect it.

Each patch must demonstrate both the repaired case and the nearby case it could break. Release only after the whole journey passes together; do not treat an isolated helper test or one successful answer as completion.
