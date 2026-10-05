# End-to-end RAG message-response repair plan

## 1. Review conclusion and corrected baseline

**The main problem is inconsistent handling of intent, evidence, and verification across the message pipeline.** Retrieval can find useful evidence, but later stages lose its structure, reinterpret its scope, reject supported assertions, and sometimes report the wrong failure. The evaluation pipeline omits several of those production stages, allowing these gaps to survive refinement.

Source-governance debt and infeasible recovery budgets are additional causes. Neither increasing retrieval counts nor rebuilding the index can resolve the complete problem.

This review examined the saved manifests and response bodies, previous implementation/review/acceptance reports, current code and uncommitted changes, and relevant research. It included provider-free reproductions and **16 passing existing focused tests**. It did not change code, project configuration, sources, or application state. The full repository gate was not rerun.

### Corrections to the previous QA interpretation

The latest report accurately records the public API outcomes, but its causal explanation needs correction:

| Cases | What the raw execution evidence establishes |
|---|---|
| Q1 current threshold, Q2 historical threshold, Q4 salary | Recovery searches were cancelled around 28 seconds; coverage and generation did not complete. |
| Q3 rebate | Searches completed, but coverage started at approximately 26 seconds and was cancelled at 30 seconds. |
| Q5 AGM | Partial evidence passed admission. Generation and verification ran; verification rejected the candidate. Finalization incorrectly reported a recovery timeout. |
| Q6 RJSC fee | Partial evidence passed admission. Generation ran; verification recorded `missing_citation`. Finalization incorrectly reported a recovery timeout. |
| Q7 Budget Speech proposal | Complete coverage reached generation, followed by verification rejection. The saved public response omits the candidate text, so it cannot establish whether every original generated assertion was correct. |

Therefore, the useful diagnostic split is **four actual recovery failures and three post-generation verification failures**, two of which were mislabeled.

The previous focused retest also produced six successful AGM answers. The newer AGM prompt added current-applicability requirements. This demonstrates fragile scope-dependent behavior, rather than proving that every refinement was ineffective.

## 2. Pinpointed findings

### A. Intent and source policy are interpreted inconsistently

**Confirmed through current-code probes:**

- “This is a rule lookup, **not a personal calculation**” selects the calculation path.
- The salary calculation’s phrase “minimum-tax comparison” changes its normalized primary task to comparison, while another branch independently identifies calculation.
- The historical question’s contrast with AY 2026–27 becomes an additional required period. A correct AY 2025–26-only quotation is then rejected for lacking AY 2026–27.
- Five explicit corpus-only prompts remain `source_restriction="project_default"`.
- Ordinary-resident facts are not represented as stipulated category facts.

Relevant locations: [turn_resolution.py:850](E:/python-projects/rag-builder/backend/app/modules/conversations/turn_resolution.py:850), [source restriction recognition:1195](E:/python-projects/rag-builder/backend/app/modules/conversations/turn_resolution.py:1195), [requirement scope expansion:502](E:/python-projects/rag-builder/backend/app/modules/conversations/services/evidence_repair_service.py:502).

No web call occurred in these captures. The restriction defect is a latent enforcement problem; it is not evidence that these runs actually used external sources.

**Required change:** resolve one typed intent and propagate it unchanged. Distinguish the primary task, subordinate operations, stipulated inputs, target periods, comparison/excluded periods, documentary statements, current-law claims, and source restrictions. Remove downstream keyword reinterpretations and global year concatenation.

### B. Approved evidence loses its structure during verification

The Budget Speech proof contains a heading followed by period rows and amount rows. Coverage accepts that structure. The quantity verifier subsequently splits it into individual clauses and retains lines matching subject words such as “tax.” Amount-only rows disappear.

A replay of the saved approved scope and faithful paraphrases consequently produces `unverified_amount`. This happens even when a stub semantic verifier returns `supported`.

The numeric parser also attaches a following row’s `BDT` marker to year suffixes such as `28`, `30`, and `31`, because currency detection crosses line boundaries.

Relevant locations: [quantity alignment:3627](E:/python-projects/rag-builder/backend/app/modules/conversations/grounding_service.py:3627), [amount rejection:1147](E:/python-projects/rag-builder/backend/app/modules/conversations/grounding_service.py:1147), [numeric classification:71](E:/python-projects/rag-builder/backend/app/modules/conversations/quantities.py:71).

**Required change:** preserve header, period, category, amount, unit, row, footnote, and source offsets as one evidence bundle. Verify quantities against their bound bundle, rather than reconstructing proof from flattened prose.

### C. Response limitations are mistaken for factual assertions

The fallback sentence “This is reported only as a budget proposal, not enacted or current law” is rejected by a generic negation check. It is intended to constrain the answer’s scope; it does not assert that enactment never occurred.

The draft schema allows nonfactual limitations, but it does not assign them an explicit type. Rendering and subsequent claim splitting must infer that distinction again.

Relevant locations: [AnswerSegment:13](E:/python-projects/rag-builder/backend/app/modules/conversations/answer_draft.py:13), [claim rebinding:930](E:/python-projects/rag-builder/backend/app/modules/conversations/grounding_service.py:930), [negation guard:3565](E:/python-projects/rag-builder/backend/app/modules/conversations/grounding_service.py:3565).

**Required change:** distinguish factual assertions, calculations, and response-scope notices structurally. Verify factual assertions before rendering. Render scope notices from validated request and coverage state.

### D. The current uncommitted verification bypass introduces false positives

The current working-tree change sends ordinary cited passages through lexical/embedding checks without semantic entailment.

Provider-free tests accepted:

- An owner obligation supported only by a tenant obligation.
- A fire-certificate requirement supported only by a lease requirement.
- An AY 2026 rate supported by AY 2025 evidence.

The configured rejecting verifier was never called.

Relevant locations: [ordinary-passage bypass:1035](E:/python-projects/rag-builder/backend/app/modules/conversations/grounding_service.py:1035), [fallback verification branch:1218](E:/python-projects/rag-builder/backend/app/modules/conversations/grounding_service.py:1218).

This is a confirmed current-code regression, not proof that the historical browser service loaded these dirty changes.

**Required change:** require semantic and typed scope/quantity verification for factual paraphrases on both ordinary and reviewed paths. Similarity remains an evidence-location signal. Preserve only a narrowly validated exact-quotation fast path.

### E. Recovery cannot finish its mandatory work within the effective budget

The captured project configuration advertises focused/broad recovery limits of 30/35 seconds. Actual recovery is clipped to approximately request-start +30 seconds, and discovery stops around +28 seconds.

Observed costs include:

- Initial retrieval: approximately 5–10 seconds.
- Planning: approximately 6–12 seconds.
- Completed coverage reviews: approximately 7 seconds.

Q3’s coverage received only four seconds. The two-second review reserve is therefore insufficient for observed work.

Broad recovery also advertises eight initial searches and two follow-ups, while runtime code caps initial searches at three and subtracts those from the same three-search allowance. Three initial searches leave no semantic continuation.

Relevant locations: [deadline ownership:156](E:/python-projects/rag-builder/backend/app/platform/providers/request_work.py:156), [ChatService budget allocation:1474](E:/python-projects/rag-builder/backend/app/modules/conversations/services/chat_service.py:1474), [recovery query cap:2300](E:/python-projects/rag-builder/backend/app/modules/conversations/services/evidence_repair_service.py:2300).

**Required change:** implement the selected adaptive budget through one resolved execution policy. Make advertised settings, effective limits, action admission, provider timeouts, and the outer request timeout agree.

### F. Finalization hides the actual failure

An earlier `insufficient_followup_budget` recovery stop overrides a later explicit `claim_verification_failed`. That reproduces Q5/Q6’s incorrect timeout classification.

Rejection also removes the candidates before diagnostic metrics are calculated:

- `failed_count` counts distinct reasons rather than failed assertions.
- Refusals can report citation coverage of `1.0`.
- Rejected assertions disappear from attempted-claim metrics.
- Some fallback verification calls run outside the verification phase.

Relevant locations: [terminal precedence:86](E:/python-projects/rag-builder/backend/app/modules/conversations/terminal_outcome.py:86), [verification and sanitation:2053](E:/python-projects/rag-builder/backend/app/modules/conversations/services/chat_service.py:2053).

**Required change:** carry an explicit finalization result. Keep optional recovery stops, fatal errors, verification verdicts, published claims, and rejected candidates separate.

### G. Source metadata can exclude useful documents incorrectly

The structural extractor creates period facts from individual mentions. Retrieval treats any typed facts in a document as an exhaustive applicability inventory.

The saved structural-preview Budget Speech dossier recognizes later assessment periods but misses earlier plural/year-list forms that are present in the answer-supporting text. Activating that preview could therefore exclude relevant proposal evidence.

Relevant locations: [structural period extraction:118](E:/python-projects/rag-builder/backend/app/modules/knowledge/services/chunking/structural_provenance.py:118), [document eligibility:567](E:/python-projects/rag-builder/backend/app/modules/knowledge/source_metadata_read.py:567).

This is a confirmed code/data incompatibility in the preview, not the demonstrated cause of the active-build October 1 failures.

**Required change:** distinguish a period mention from governing applicability, and local provision/table scope from whole-document coverage. Unknown scope must remain eligible for review; hard exclusion requires affirmative incompatible scope.

The earlier adjacent-year overlap bug has already changed in current code. It should not be reported again as an unfixed finding.

### H. Genuine corpus and governance limitations remain

The saved dossier contains 24 sources, 14 relationship records, no verified replacement scopes, and ten active sources without effective-start dates.

Specific limitations include:

- Historical AY 2025–26 threshold authority remains unestablished in the inspected dossier.
- Rent Section 109 text exists, but its amendment/footnote chain remains unresolved.
- The Companies Act sources omit separate schedules; a missing RJSC fee schedule cannot be recovered through ranking changes.
- Budget Speech proposals must remain distinct from operative law.

Relevant locations: [relationship handling:325](E:/python-projects/rag-builder/backend/app/modules/knowledge/services/source_metadata_service.py:325), [replacement eligibility:636](E:/python-projects/rag-builder/backend/app/modules/knowledge/source_metadata_read.py:636).

**Required change:** reconcile the sources needed by the acceptance set using exact provisions, effect, period, supporting spans, and review provenance. Never fill unknown dates from publication dates or assume an amendment replaces an entire instrument.

### I. Evaluation does not certify the production journey

The Evidence Quality adapter uses production search, but its answer path directly performs context selection → ordinary generation → claim mapping. It omits production scope resolution, authority recovery, structured drafting, semantic-verifier composition, deadline handling, terminal reduction, and persistence behavior.

Its headline latency is search latency. Any insufficiency reason can count as successful negative behavior. Empty selected context can be reported as retaining retrieved relevant evidence.

Relevant locations: [evaluation adapter:320](E:/python-projects/rag-builder/backend/app/composition/evaluation.py:320), [latency metric:328](E:/python-projects/rag-builder/backend/app/modules/evaluation/services/evaluation_runner_service.py:328), [negative scoring:30](E:/python-projects/rag-builder/backend/app/modules/evaluation/metrics.py:30).

The two evaluation-smoke tests use a small in-memory lexical implementation. They are useful smoke checks, but do not certify this production journey.

**Required change:** reuse the production execution engine in evaluation and grade complete outcomes, including correct abstention, verification failure, and timeout separately.

## 3. Implementation sequence

### Phase 1 — Establish a trustworthy execution foundation

#### Checkpoint 1.1 — Establish a reproducible baseline and truthful diagnostics

1. Preserve the current dirty changes and record HEAD, diff hash, runtime process identity, loaded code fingerprint, prompt/schema versions, effective configuration, source generation, index build, and provider routing.
2. Annotate the previous QA report with the corrected four-recovery/three-verification interpretation. Preserve original JSON bodies.
3. Add regression fixtures from the saved evidence before repairing behavior.
4. Correct terminal precedence and attempted-versus-published claim metrics.
5. Add an operator-only diagnostic record containing assertion IDs/text, proof bindings, evidence hashes/spans, deterministic check outputs, semantic verdicts, and correction attempts. Public message responses remain sanitized.

Use project-scoped diagnostic storage with an additive migration, admin/project authorization, audited access, and seven-day expiry for full diagnostic payloads. Full capture is opt-in for QA; normal operation retains bounded metadata. Exclude credentials and hidden model reasoning.

**Exit condition:** every reproduced failure has a stable identifier and accurately reported stage; Q5/Q6 no longer appear as recovery failures.

#### Checkpoint 1.2 — Consolidate the production execution path

Extract a `MessageExecutionRunner` from existing production orchestration. Regular responses, SSE, and evaluation call this same implementation.

Keep authentication, conversation/history loading, immutable configuration resolution, transactions, persistence, and transport adaptation outside the runner.

Consolidate existing representations into:

- `TurnIntent`: task, inputs, scope, modality, periods, source policy.
- `RequirementGraph`: required/optional requirements, dependencies, assigned scope.
- `EvidenceBundle`: exact source identity, spans, structural relationships, quantities, and applicability proof.
- `AnswerAssertion`: stable ID, kind, text, requirement IDs, bundle IDs, and calculation references.
- `FinalizationResult`: verified assertions, limitations, rejected attempts, actual fatal event, and recovery stop.

These contracts replace competing dictionaries and regex decisions. They should not become a second implementation beside the old path.

**Exit condition:** deterministic Regular, SSE, and evaluation executions agree on normalized scope, admitted proof, verified assertions, and terminal result.

### Phase 2 — Fix answer correctness and completion

#### Checkpoint 2.1 — Repair proof preservation and answer verification

1. Verify atomic assertions before citation rendering. Preserve IDs through generation, correction, verification, and persistence.
2. Retain table headers, period/category rows, amount cells, continuations, and footnotes as indivisible proof where required.
3. Parse complete period spans before currency values; constrain currency association to structural cells or bounded lines.
4. Replace inferred limitation phrases with typed notices. For example: “This answer describes the document’s proposal; its legal effect is not assessed here.”
5. Remove the ordinary-passage entailment bypass. Reject wrong category, period, condition, and quantity role regardless of lexical similarity.
6. Return structured verifier failures identifying the assertion, failed dimension, and evidence binding.
7. Permit one targeted semantic repair using the same approved evidence. Prefer deterministic rendering from validated facts when free paraphrasing fails.

For calculations, extend the existing Decimal verification into a bounded calculation graph. Supported operations are addition, subtraction, multiplication, division, minimum, maximum, and ordered bracket calculations. Every operand must reference a supplied input, verified source quantity, or prior calculation node. Legal applicability remains a separate proof requirement.

Use one shared malformed-output correction allowance per turn, plus at most one separately budgeted semantic repair. Existing citation/pruning/fallback branches must not create hidden extra attempts.

**Exit condition:** the saved Budget Speech structure supports faithful assertions and scope notices; swapped periods/amounts and invented legal effects fail.

#### Checkpoint 2.2 — Implement adaptive execution and targeted recovery

The selected policy is:

| Policy | Default |
|---|---|
| Simple single-pass question | 45-second hard ceiling, with a 30-second p95 target |
| Complex evidence/calculation question | 120-second hard ceiling, with a 90-second p95 target |
| Promotion | At most once, retaining the original request start |
| Initial recovery search batch | At most two distinct searches |
| Total recovery search credits | Three simple; six complex |
| Structural completion | Two bounded actions, separately counted |
| Known corpus gap | Complete a precise limitation response without repeated searching |

Promote when a genuinely recoverable required dependency introduces work that cannot fit the simple budget. Do not promote merely because a provider is slow or the necessary document is known to be absent.

Apply reserves only to stages still required. Initial cold-start floors are coverage 10 seconds, generation 8 seconds, verification 12 seconds, and persistence 2 seconds. Reuse completed planning/coverage work. Replace floors with larger measured p95 estimates when sufficient samples exist; freeze those estimates per request and record their origin.

One deadline object controls the outer timeout, provider calls, database acquisition, semaphores, search branches, corrections, and persistence. Promotion must reschedule the actual outer timeout.

Review the strongest initial evidence before launching recovery. Follow-up searches should target a named missing requirement, structural neighbor, footnote, or amendment. Preserve completed independent search results if another branch fails.

Existing immutable configuration snapshots retain legacy semantics. Introduce adaptive policy through a new immutable project revision; legacy timeout/query fields must not silently compete with adaptive limits.

**Exit condition:** recorded Q1–Q4 timing scenarios complete mandatory review or produce an accurately scoped limitation within the chosen ceiling.

### Phase 3 — Resolve corpus gaps and certify the full journey

#### Checkpoint 3.1 — Reconcile corpus structure and source governance

1. Add versioned scope facts identifying mention/governing scope, document/provision/table locality, and operative/proposal/example status.
2. Retain unknown candidates for review. Exclude only affirmatively incompatible scopes.
3. Reconcile the acceptance set’s source relationships and effective periods from source-backed evidence.
4. Re-attest metadata when text is unchanged; privately reprocess when structure/text changes; reacquire/reparse documents whose schedules or footnotes are absent.
5. Build a new private candidate. Verify semantic structure as well as hashes, counts, and embedding identity.
6. Compare active and candidate builds with identical runtime/configuration and questions.

A build’s current `VALIDATED` state establishes integrity, not answer quality. Require an acceptance artifact tied to exact code/configuration/source/build identities before activation.

#### Checkpoint 3.2 — Repair provider contracts, evaluation, and user messaging

- Use schema-constrained output for certified model/endpoint combinations. Retain local validation and explicit capability-based fallback.
- Record effective model, reasoning setting, schema mode, and provider purpose for every stage. The application currently forces `gpt-6-luna` to low reasoning for named stages; the Codex QA agent’s reasoning setting does not change this.
- Keep the current model as the baseline. Compare alternative stage settings only after deterministic defects are repaired.
- Rename retrieval-only metrics and add complete-turn latency, answerability, correct partial-answer rate, correct abstention, verification failures, timeouts, and attempted/rejected claims.
- Treat citation coverage as not applicable when there are no factual assertions.
- Render responses from verified assertions plus typed limitations. Preserve useful documentary statements without presenting unresolved current applicability as established.

The public Message API remains compatible. Add optional execution-policy/provenance fields and an authenticated diagnostic endpoint. Regenerate OpenAPI types and update conversation, evaluation, source-governance, and API documentation.

## 4. Acceptance and rollout

### Deterministic acceptance

Require passing tests for:

- Negated calculation intent; calculations containing comparison substeps.
- Historical target plus excluded later period.
- Every captured corpus-only wording.
- Ordinary and reviewed evidence rejecting wrong category, year, condition, and certificate.
- Multiline Bengali/English tables retaining period–amount bindings.
- Proposal-scope notices versus factual “never enacted” assertions.
- Partial evidence followed by verifier rejection, with no false timeout.
- Verification fallback after recovery closes but before finalization expires.
- One adaptive-budget promotion, no second promotion, and persistence within the hard ceiling.
- Empty selected context recorded as evidence loss.
- Identical production/evaluation outcomes.
- Regular/SSE/persisted GET parity, including failed and partial cases.
- Operator diagnostic authorization, expiry, and public-output sanitation.

Use captured source topology and realistic paraphrases. Exact equality between generated text and the approved scope is insufficient coverage.

### Live acceptance

Freeze the seven latest questions and the earlier AGM, threshold, DNCC, rent, and partnership questions. Assign expected outcomes through source review before running them:

- **Answerable:** verified facts and citations required.
- **Partially answerable:** supported facts plus the exact remaining gap required.
- **Corpus gap:** precise completed abstention required.
- **Unknown source sufficiency:** resolve the label before treating it as a positive benchmark.

Run three repetitions per case against active and candidate builds. Preserve raw Regular responses and representative SSE/GET parity captures.

Release gates:

- Every deterministic safety/protocol fixture passes.
- Every core live case produces its expected completed outcome in all three repetitions.
- No unsupported factual assertion or source-policy violation.
- No timeout counted as successful abstention.
- End-to-end latency meets the selected policy.
- Repository quality checks pass; previously reported failures must be rerun and resolved, not assumed current or fixed.
- Activation is tied to the accepted runtime/configuration/source/build fingerprint, with the previous pointer retained for rollback.

### Research alignment

The repository already implements hybrid retrieval, reranking, source policy, and recovery. The repair should strengthen their contracts and measurement:

- Google’s sufficient-context work distinguishes relevant evidence from enough evidence to answer. This supports requirement-specific sufficiency and separately measuring false refusals. [Google Research](https://research.google/blog/deeper-insights-into-retrieval-augmented-generation-the-role-of-sufficient-context/)
- Contextual retrieval preserves information lost when chunks are isolated. Here that principle applies particularly to headings, periods, table rows, and footnotes. Its published gains are not predictions for this corpus. [Anthropic](https://www.anthropic.com/engineering/contextual-retrieval)
- Agentic RAG uses missing-evidence feedback to guide subsequent retrieval. This supports targeted continuation rather than repeated broad searches. [Google’s agentic RAG research](https://research.google/blog/unlocking-dependable-responses-with-gemini-enterprise-agent-platforms-agentic-rag/)
- JSON mode guarantees valid JSON, while Structured Outputs provides schema adherence for supported combinations. Neither establishes factual correctness. [OpenAI documentation](https://developers.openai.com/api/docs/guides/structured-outputs)
- Claim-level retrieval and generation metrics help locate failures across stages. [RAGChecker](https://arxiv.org/abs/2408.08067)

## 5. Handoff context for a new chat

**Workspace:** `E:\python-projects\rag-builder`
**Inspected HEAD:** `d2bdd6aa5fd5389da6f738e3e37bbdf7ee3b38c7`
**Existing dirty files:** `grounding_service.py`, `chat_service.py`, `claim_entailment_service.py`, and `openai_compatible_chat.py`. Preserve and review their changes individually.

**Captured project:** Income Tax & Budget, `2ee2756f-ad27-44df-a9d3-1316b10ccbb1`
**Active build:** `8eb1595a-8e38-41e9-96c5-0ba419e79bee`
**Unactivated structural candidate:** `c75f7019-f94b-4435-9036-e9cf59ae216b`
**Captured source generation/configuration:** generation 35; revision 45
**Captured providers:** OpenAI `gpt-6-luna`; Cohere `embed-v4.0`, matched embedding identity; Cohere reranker applied. Translation was disabled.

These identities do not attest which code revision the historical running backend loaded.

Start with:

- [Latest QA report and linked raw responses](E:/python-projects/rag-builder/artifacts/cursor-runs/post-qa-remediation-2026-09-30/browser-qa/broad-income-tax-2026-10-01/report.md)
- [Exact question/response manifest](E:/python-projects/rag-builder/artifacts/cursor-runs/post-qa-remediation-2026-09-30/browser-qa/broad-income-tax-2026-10-01/manifest.json)
- [Previous acceptance report](E:/python-projects/rag-builder/artifacts/cursor-runs/post-qa-remediation-2026-09-30/report.md)
- [Previous root-cause proposal](E:/python-projects/rag-builder/docs/plans/rag-post-qa-root-cause-review-2026-09-30.md)
- [Source dossier assessment](E:/python-projects/rag-builder/artifacts/cursor-runs/post-qa-remediation-2026-09-30/source-dossier-assessment.md)

The previous acceptance report explicitly recorded unresolved acceptance and failing checks. Narrow AGM success, successful index validation, and passing smoke tests must not be treated as complete product acceptance.

When implementation is authorized, save this revised review and plan under `docs/plans/rag-message-journey-remediation-2026-10-01.md`, with replay fixtures and acceptance manifests linked from it. This planning review has not created that file.

**First implementation milestone:** reproduce and fix terminal misclassification, table-proof loss, scope-notice rejection, and the ordinary-passage false-positive regression. Establish shared production/evaluation execution before further tuning or activation.
