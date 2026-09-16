# Live conversation and configuration audit — 17 September 2026

## Scope and evidence

Project: Bangladesh Income Tax & Business Legal Guide (`f932c9e0-40af-4ff3-8704-ac3e542f2a8c`). Source generation 101, active build `d0317f95-a523-465f-9126-e5be93031283`. This review changed live project configuration and submitted test messages; it did not change application code or source metadata. Findings concern system behavior, not independent legal advice.

Evidence includes the user's complete API response for conversation `9439c608-b7bd-41e7-ba55-48f1f1ebcd73`, live Test Lab messages, visible inspector diagnostics, AI configuration, Audit, Evidence Quality and System Health, and read-only inspection of the local implementation.

## Configuration and interpretation

The original revision 10 used Authoritative, Balanced grounding, indexed-then-web, query translation off, and `openai/gpt-5.6-luna` (the only approved model). Its custom retrieval used 80 semantic/80 keyword candidates, rerank always/window 40, top K 12, context 24 chunks/48,000 characters, history 20, document cap 6, section cap 3 and passage windows 512/overlap 64.

The supplied trace records semantic admission 0.35, cross-language semantic 0.30, reranker relevance 0.40; the Balanced high-confidence band is disabled. These are admission signals, not proof that an answer entails a source. Do not lower them to make the dashboard greener. Zero retrieval score thresholds do not mean the evidence gate is disabled.

Authoritative is a sensible default for statutory applicability, amendments and tax calculations. Factual is worth evaluating for simple extraction or explanations, but changing the entire project to it to avoid refusals would also change the legal-evidence contract. Multi-perspective is for attributed agreement/disagreement, not a remedy for missing legal coverage.

Indexed-then-web does not currently fill every partial coverage gap. `chat_service.py` requests fallback when `grounding.blocks_generation(evidence)` is true, and separately suppresses unresolved-authority fallback. The user's partial answer therefore had `web_search.status=not_requested`. The label currently suggests broader assistance than the implementation provides. Indexed-and-web would add work on every eligible turn; do not enable it as a blanket workaround.

Revision 11 (`693bfffa`) replaced the long tax-first instructions with a shorter balanced tax/business policy. It asks for ordinary language, useful answers first, short paragraphs/bullets, one compact limitation, and one useful next step. It preserves income exclusions, threshold/slab/rebate/minimum-tax proof, gross/net distinctions, TDS separation, provisional estimate scope, amendment handling and conditional business duties. It does not impose a rigid word limit on complex calculations.

Revision 12 (`dc1080e1`) was a comparison using certified Quality: the same 80/80 candidates, rerank 40 and top K 12, with context 10 chunks/16,000 characters and the profile's shorter passage windows. All behavior settings and the new instructions were retained. A single run is exploratory evidence, not certification or a causal performance benchmark.

Final active revision 13 (`f959bc40`) restores revision 11's custom retrieval while retaining the concise 451-word/3,268-character instructions. Its effective hash is `8774de363f47`, matching revision 11. This is the conservative retained configuration, not a claim that it is optimal. Authoritative + Balanced, the existing model, translation off and indexed-then-web remain unchanged. Quality was not retained because its limited trial showed no reliable task-quality or latency advantage. Model comparison was unavailable because only one model is approved. Query translation needs its own controlled English-over-Bangla test before changing the default.

## Observed outcomes

| Case | Configuration | Outcome |
| --- | --- | --- |
| User's broad inactive-company question | Revision 10, Custom | 107.7 s server time; 814 words; 2 citations; 13/40 claims supported; `grounded=false`; partial scope. |
| Identical broad question, conversation `825a7ca7-6352-4238-9553-26830c244988` | Revision 11, Custom | 155.7 s browser round trip; refusal; 12 recovery attempts; final `repair_followup_limit`. |
| Identical broad question, conversation prefix `fc947e65` | Revision 12, Quality | 153.5 s round trip; partial answer; 3 citations; 3/20 claims supported; answered an English question in Bangla. |
| Explicit English rewrite of that answer into three short bullets, no new facts | Revision 12, same conversation | 139.7 s; refusal; 10 recovery attempts; 8 requirements; `repair_unavailable`, `invalid_model_response`, `json_invalid`. |
| General explanation of employment-income exclusion versus the tax-free threshold, conversation prefix `fcc40224` | Revision 12, Quality | 79.7 s; refusal; 8 recovery attempts; wording referred to an unreliable final calculation although no calculation was requested. |

These were four new live test turns. No personal tax calculation was submitted: automatic approval review rejected a proposed salary/residency scenario, so the tax smoke test instead asked about general published rules. Neither numerical tax-calculation acceptance nor a statistically meaningful profile comparison was completed.

Revision 11's refusal had an earlier `partial_scope_validated=true` review, followed by a newer incomplete review and no partial handoff. Final source ranges validated, but full and partial coverage did not. The final reviewer marked AGM, accounting books, auditor appointment and financial-statement filing checks supported; that alone is insufficient to approve the answer, but the earlier validated partial checkpoint should not disappear without a specific invalidation reason.

The Quality answer used bullets rather than wide tables, but repeated limitations in the opening, a seven-item missing-topic list and the conclusion. Its language and grounding failures mean the smaller profile is not established as a better production choice. The near-identical latency also does not support promising a speed-up.

## Priority 1 — protect the user's actual request and validated proof

### Explicit consequences are incorrectly removed as optional

The supplied response records `optional_requirements_ignored` containing R8, origin `explicit_user_request`, describing consequences, penalties, late fees and prosecution. The user explicitly requested consequences of non-compliance. `_optional_requirement` in `services/evidence_repair_service.py` returns true when **any** optional-facet substring is in the description but not literally in the question. Thus a description containing `fee` can delete an entire requested consequences requirement. Morphology and synonyms also differ (`penalty`, `penalties`, `consequences`).

Refine: preserve explicit user requirements. Split optional detail from required facets instead of deleting the parent. Use resolved intent/facet identity and parent IDs; do not enforce scope with substring bans. Validate necessary applicability against evidence without turning all procedures into optional work.

Acceptance: the exact supplied question retains consequences despite describing them as penalties/late fees; optional second-source corroboration still cannot block a confirmed duty. Include Bangla equivalents and mixed required/optional descriptions.

### Recorded requirement ownership can be semantically wrong

The supplied trace binds `ধারা ১৭০ রিটার্ন দাখিল` to R2 (RJSC annual return), although the query is an income-tax route. Revision 11 likewise bound a financial-statement query to R7 (VAT). The implementation filters model-provided IDs to `untried_missing`, then assigns a sorted missing ID by query position if none remain. That makes a record look precise without establishing what the query actually attempted.

Refine: require valid planner IDs, preserve legitimate retries against their original facet, and reject or explicitly label unbound queries. Do not reassign an incompatible query to another requirement by array position. A query that searched tax evidence must not consume an RJSC retry. Include a bounded repair of malformed ownership only if necessary.

Acceptance: shuffled queries, duplicate queries, invalid IDs, already-attempted IDs and legacy string plans cannot consume an unrelated requirement's retry. Display candidate/admitted/proof IDs distinctly; current `discovered_ids` still means cited proof, not all discoveries.

### Later review can erase a valid partial answer

After parsing a completed new `CoverageVerdict`, repair unconditionally resets `partial_checkpoint=None`. Timeout restoration exists, but normal follow-up-limit and several early returns do not restore a prior still-valid checkpoint. The revision 11 live trace demonstrates an earlier validated partial scope followed by refusal.

Refine: keep the last validated independent proof set until changed source content, snapshot, requirement identity, applicable authority or an explicit contradiction invalidates it. Revalidate that checkpoint at every terminal path, including round limit, no-new-query and malformed review. Record why it was invalidated. Do not restore a merely `supported=true` check without exact proof validation.

Acceptance: earlier independently proved obligations survive a later incomplete review; changed authority or contradicted proof correctly invalidates them. Keep the existing global stop budget.

## Priority 1 — repair claim verification before tuning scores

### Whole-span negation presence is not entailment

`_bounded_entailment_guard` compares whether any negative word occurs anywhere in the claim and anywhere in the selected evidence. A positive first-AGM sentence can be rejected because its evidence paragraph contains a separate exception or prohibition. The supplied run rejected many seemingly direct statutory statements as `unrelated_or_insufficient_evidence`, while also accepting a penalty statement whose displayed evidence excerpt was only the section heading. These require exact full-span inspection; clipped UI excerpts alone do not establish what the verifier saw.

Refine: align the subject, predicate, condition and exception before comparing polarity. Locate the supporting clause and necessary qualifiers, then use a bounded entailment decision for ambiguity. Return unverified when alignment is uncertain, unsupported when there is a genuine contradiction or lack of support. Preserve numeric and duration guards, including 11 versus 12 years and 21 days versus three weeks. Similarity remains a locator, not sufficient proof.

Acceptance: positive rules with negative exceptions, exemption negation, Bangla `না/ব্যতীত`, thresholds with reversed comparison, irrelevant numbers in adjacent clauses, headings without operative provisions, equivalent durations and unrelated evidence.

### Tables become meaningless claims

The supplied run created claims such as `| Registrar |  |` and `| Company and its directors |  |`. Header suppression does not solve row fragmentation by sentence splitting. These fragments inflate failures and lose the obligation to which the authority belongs.

Refine: segment Markdown structurally. Attach header/row-label context to meaningful cell assertions; merge an authority cell with its duty rather than treating a standalone label as a legal proposition. Keep row-local citation inheritance. Test multi-sentence cells, headerless tables, Bangla punctuation and empty source columns.

### Scope notes remain phrase-dependent

The exact preamble `The available provisions do not establish the requirements, forms, deadlines or penalties for:` produced unsupported/missing-citation bullets for topics already in validated missing coverage. The phrase recognizer covers selected material/source variants but not this ordinary wording. The Quality answer also failed many natural Bangla scope sentences.

Refine: classify scope structurally using the production `knowledge_repair` envelope and explicit unresolved facet IDs. Preserve preamble-to-bullet association, but terminate it at the end of the list. Match duty, fee, deadline, applicability and penalties separately; a shared noun is insufficient. Do not simply broaden regexes to exempt every sentence saying `not` or every limitation paragraph. Mixed factual conclusions still need citations; corpus-wide absence claims remain distinct.

Acceptance: the exact English list, actual Bangla replay wording, opposite-facet negatives, mixed limitation/legal conclusions and narrative following a list. Report factual, scope and structural counts separately.

## Priority 2 — make conversation behavior predictable

Resolve `response_language` once from the original user message or explicit request, separately from retrieval query language, document language and reviewer summaries. Pass it as an output contract to generation and rewrite. The Quality broad replay answered in Bangla despite an English question and multiple instructions to follow the user language. Inspect the final rendered prompt and add a bounded language-format validation rather than more repeated prose instructions.

Preserve the user's desired presentation across turns. A short rewrite should reuse validated claims and their proof, revalidate active-source identity and retrieve only genuinely new facts. Do not restart a broad whole-question legal audit because the original question was broad. Unsupported prior claims must be narrowed or corrected, not blindly preserved.

The explicit three-bullet rewrite recalled three passages, then expanded into eight requirements and ten recovery attempts before invalid JSON caused refusal. The requested presentation mode is therefore not controlling downstream coverage scope. Carry the retained validated claim scope into repair, not the full historical question. If the prior response is ungrounded, offer the independently verified subset and explain that limitation briefly. For explanatory tax questions, avoid calculation-specific requirements and refusal templates unless a calculation was actually requested.

Default answer shape: a direct opening, a compact actionable list (duty, authority, supported deadline and consequence), then one brief limitation/next step. Broad questions can be longer, but missing-topic inventories should stay in diagnostics. Avoid artificial hard word limits, mandatory tables, repeated disclaimers, or a questionnaire before independently useful work.

## Priority 2 — reduce actual recovery cost

The supplied response spent 90.3 s in coverage/recovery versus 12.8 s generation and 0.9 s final claim verification. It used 6 LLM calls, 11 reranks, 17 embedding calls and 303 embedded texts; aggregate LLM input was 117,319 tokens, not one oversized final prompt. The revision 11 replay used 7 LLM calls, 13 reranks, 17 embedding calls, two schema retries and 152.5 s coverage/recovery. Its final generation never ran.

Refine requirement granularity and ownership first. Send each review only new evidence plus the minimum stable proof needed for applicability. Persist validated facet IDs and exact source ranges. Use structured response constraints and compact source selectors to reduce malformed-output retries. Measure review input tokens and latency separately from generation. A larger timeout, context window, top K, or embedding cache cannot fix repeated oversized coverage review.

An embedding LRU remains a measured optimization, keyed by text/model/purpose/configuration. Existing traces already report many cache hits. Do not make it the first intervention.

## Operator and release-quality gaps

- Audit recorded the configuration revision correctly. It is an operational change log, not a semantic-quality certification.
- Evidence Quality has no versioned datasets and no completed runs. Create frozen business/tax/English/Bangla/rewrites fixtures before choosing a winning profile. Run at least three repetitions per representative case; compare task coverage, false support/refusal, language adherence, latency and provider work.
- System Health showed 9/9 dependencies and two active workers, with environment profile `development` on the live host. Verify the intended deployment setting; that label alone is not proof of a vulnerability. Provider checks were cached, not contemporaneous semantic acceptance tests.
- Cited-authority `not_assessed` is correctly separate from expanded retrieval. The supplied trace still has unscoped relationships and missing effective dates. Audit documentary commencement/provision scope with immutable revisions; do not infer or invent MODIFIES links.
- The UI now shows elapsed time, cancellation, immutable conversation/current-project revision comparison and recovery attempts. Progress remains broadly `Checking evidence` for minutes; add meaningful phase/attempt progress without exposing internal prompts.
- The supplied `unverified_claim_rate=0.0` coexists with 27 unsupported claims. This may reflect a narrow unverified enum denominator, but it is misleading without separate unsupported/failed-claim rates and denominators.
- Keep citation numbering stable and expose the full selected verification span on demand. Synthetic Markdown page 1 is still displayed; prefer section/chunk location for nonpaged sources.
- Add conversation/message selection and replay links so previous tests are reviewable after navigating away. Test Lab currently starts without a selected prior test conversation.

## Implementation order

1. Freeze the supplied response and these live cases as redacted fixtures; preserve failing outputs.
2. Fix explicit-scope deletion, incorrect attempt binding and partial-checkpoint loss together.
3. Fix clause-aligned verification, structural tables and scope-facet classification together.
4. Enforce resolved response language and bounded presentation-only continuity.
5. Reduce review payload and retries; then evaluate retrieval profiles and query translation one dimension at a time.
6. Add partial-gap web discovery only for named gaps with official-source provenance and the same applicability/admission requirements. Do not bypass unresolved authority.
7. Finish metadata audit and disposable integration/`created_by` fixtures independently.

Do not call a configuration best or all issues solved until repeated provider-backed acceptance passes. No code fixes were implemented during this audit.
