# Remaining RAG issues: investigation and implementation guide

Date: 16 September 2026  
Status: investigation only; no fixes implemented  
Inspected checkout: `d72d13614c78e2d4a928cbf6bdc70a73bc39ba9e`

## 1. Executive findings

The latest supplied production response is materially better: eight selected/cited passages, current tax sources, and 40 supported claims. Do not restart this work as a general latest-document retrieval repair.

| Issue | Finding | Primary change area | Confidence |
| --- | --- | --- | --- |
| RJSC annual return absent from production answer | R1 never became approved evidence; partial acceptance bypassed a focused missing-rule retry. Exact original candidate-loss stage is not recorded. | Discovery planning, bounded recovery, stage diagnostics | Confirmed stopping behavior; precise production retrieval loss unresolved |
| Section-36 continuation missing locally | Actual repository execution returns 48 UUID-ordered chunks from synthetic page 1 and omits the immediately preceding section-36 chunk. Both required chunks are indexed. | Adjacent retrieval repository | Reproduced against local data |
| Global authority status unresolved | Production records implicate tax modifiers with incomplete metadata and empty provision scopes. This is not proof of missing Companies Act relationships. | Source metadata; scoped authority diagnostics | Confirmed from supplied trace and code |
| Companies Act citations have empty relationships | Citation relationships are outgoing edges. Empty outgoing edges do not prove missing incoming amendments. Local corpus has no company-law edges in either direction. | Relationship audit and reporting | Confirmed; whether additional legal edges are required remains an audit question |
| Supported short claims marked unverified | English fragments are compared with whole Bangla chunks through embedding similarity. Section-181 content was hash-matched and contains the facts. | Claim segmentation and span-level multilingual verification | Content support confirmed; individual production scores unavailable |
| Scope statements marked unsupported | Missing-evidence bullets lose their governing preamble and become uncited source assertions. | Claim classification and coverage-verdict integration | Confirmed |
| Some failures are legitimate | A saved local answer states 11 years of record retention, while the source says 12. | Generation accuracy; retain numeric rejection | Confirmed local example |
| Existing unit failure | A missing-duration guard returns unverified before unrelated-evidence rejection can return unsupported. | Verification reason precedence and test contract | Reproduced |

**Recommended order:** fix adjacent retrieval; improve bounded missing-rule recovery; classify coverage statements correctly; improve factual verification; audit metadata separately; complete regression and operator acceptance checks.

## 2. Verification scope and evidence ledger

### What was actually inspected

- The complete API response supplied by the user, including recovery checks, citations, claims, lifecycle metrics and authority records.
- Current repository code and existing tests.
- The local operator frontend and `/health` through `http://localhost:5173`: HTTP 200, development service version 0.9.0.
- The configured loopback PostgreSQL database backing the local project, using `SET TRANSACTION READ ONLY` for every diagnostic session.
- The actual `RetrievalChunkRepository.adjacent_ids()` implementation, executed read-only against the local active build.
- Four existing unit-test modules. No new provider-backed RAG conversation was generated.

### Access limitations

Both attempts to initialize the browser connector failed with `failed to write kernel assets: The system cannot find the path specified`. Therefore, no claim is made that the two browser tabs were visually inspected. Direct local project API calls returned 401 without the browser session. Production network access was unavailable in the sandbox. Docker inspection also found no running Docker daemon.

Local verification below is **current backing-data verification**, not a UI walkthrough or a fresh production run. Browser sessions, authentication settings, source metadata, project settings and application data were not changed. The only intended repository deliverable is this Markdown document.

### Production response identity

| Field | Value |
| --- | --- |
| Project | `f932c9e0-40af-4ff3-8704-ac3e542f2a8c` |
| Conversation | `b9f304c6-7a17-484d-9332-6d964b3ef237` |
| Assistant message | `5785a311-19bc-43fb-82ad-3350b42da5a9` |
| Created, UTC | `2026-09-15T22:30:30.048795Z` |
| Index build | `d0317f95-a523-465f-9126-e5be93031283` |
| Source metadata generation | 101 |
| Project configuration revision | 10 |
| Recovery version | `v21-independent-partial-obligations` |
| Claims | 40 supported, 6 unverified, 12 unsupported |
| Final evidence | 8 selected, 8 cited |
| Context limits | 24 chunks, 48,000 characters |
| Translation | Disabled; recovery nevertheless supplied separate bilingual queries |

The supplied response is the production evidence snapshot. Later changes in the deployment cannot be inferred from it.

### Local state independently verified

| Field | Value |
| --- | --- |
| Project | `6cd8b5f4-7273-4b10-be84-064c130c8303`, Local Test |
| Active project revision | 3, `9ff1dd21-c6a3-483d-80db-76695dbc0bf7` |
| Source generation | 4 |
| Active build | `abb7e951-5d66-4c40-953c-be887564895d` |
| Index profile / embedding set | `hosted-cohere-v4` / 3 |
| Active build chunk count | 588 |
| Companies Act document | `f8d740bd-28eb-4818-a251-bb2b3dd5c5f8`, READY |
| Active Companies Act revision | `e5a4944f-5594-4d25-ae4c-ca3f502e261c`, revision 1 |
| Local authority metadata | Active, effective-from null, work key `bd.companies-act-1994` |
| Local behavior | Authoritative, balanced grounding, indexed-only, translation enabled |
| Local context limits | 10 chunks, 32,000 characters; passage window 512, overlap 32 |

The local document inventory contains Companies Act statutory sections, BIDA FAQ, Sale of Goods Act, an architecture document and a sample plan. It has no NBR/Finance Act corpus or Companies Act schedule files. **It cannot establish production tax coverage or complete RJSC procedural readiness.** Missing local tax coverage is expected and must not be counted as a regression in company-law tests.

Saved local runs inspected:

| Assistant message | Observation |
| --- | --- |
| `b14c52b3-abbc-4fc2-9d5b-476afc89c261` | Focused sections 36/81/190: complete coverage, 11/11 supported; section 36 retrieved and cited. Historical v20 run. |
| `5adecc98-9680-413e-ade0-8153f099594e` | Focused Bangla section-36 follow-up: grounded, 3 claims. |
| `cb2b102c-83c7-4eb1-910d-de41a917684b` | Latest saved local broad run, v21: 6 selected chunks, 28 supported, 12 unverified, 2 unsupported. R1 has a supported limited duty but still requests adjacent context. |
| `fa6f50ca-f39b-40b1-92b2-529a8fd45f1d` | Earlier broad run: 10 selected chunks, 56 supported, 5 unverified, 7 unsupported; missing-evidence bullets also misclassified. |

These saved outcomes are evidence of previous execution, not newly generated success measurements for the current checkout.

## 3. Annual-return recovery: two distinct defects

### 3.1 Production: missing-rule discovery stops too early

Production coverage R1 is unsupported, with empty `chunk_ids` and `source_ranges`. It has no continuation anchor. The two relevant discovery queries combine annual return and AGM:

- `Bangladesh Companies Act annual return AGM`
- `বাংলাদেশ কোম্পানি বার্ষিক রিটার্ন AGM`

The statutory heading is `সদস্যগণের বার্ষিক তালিকা ও সার-সংক্ষেপ`; the source also uses `বার্ষিক বিবরণী`. This creates a plausible vocabulary/ranking mismatch. The trace does not expose enough candidate content to prove the exact ranking or admission failure.

At `backend/app/modules/conversations/services/evidence_repair_service.py:1050–1098`, partial scope is accepted unless a first-round, evidence-anchored continuation is recoverable. Missing rules without anchors therefore skip the later focused-search path. This is a concrete control-flow limitation, independent of whether query wording caused the initial miss.

Final proof filtering at approximately line 1226 retains only approved requirements. Since R1 was never approved, its absence is not evidence that final proof selection discarded a validated annual-return passage.

Also, `quotes_validated=false` does not itself prove malformed quotations: `CoverageVerdict.validates()` returns false for an incomplete verdict. In this response, `source_ranges_validated=true` and the separate partial validator permitted generation. Avoid diagnosing this flag as a quote-extraction bug.

### 3.2 Local: adjacent discovery is not structurally adjacent

Both section-36 chunks exist in the active vector and keyword indexes:

| Chunk index | ID | Relevant content |
| --- | --- | --- |
| 48 | `424bed51-cc56-4bc2-aa2c-d711caea1190` | Opening applicability, Schedule 10 and return contents |
| 49 | `17cd3c12-8935-477b-a03a-a2d0180564d1` | Continuation, filing timing, private-company certificates and penalty |

Both carry the same section title and heading path. Both have synthetic page number/start 1.

The current `adjacent_ids()` in `backend/app/modules/retrieval/repositories/retrieval_chunk_repository.py:20`:

1. Uses page +/-1 whenever both page values exist.
2. Uses chunk index +/-1 only when a page value is null.
3. Orders matching rows by UUID and caps the result at 48.

For this Markdown document, page 1 encompasses the document. The query therefore admits unrelated sections before applying an arbitrary UUID-order cap.

**Actual reproduction:** with chunk 49 as the anchor and the active local build, the repository returned 48 IDs, spanning chunk indices 5 through 516; chunk 48 was absent. This happens before reranking or claim verification. The saved v21 adjacent branch likewise selected unrelated indices including 97 and 56, while retaining chunk 49 from earlier evidence. Its final R1 still requested continuation.

This is a confirmed local retrieval defect. It did not execute for the anchorless production R1, so do not claim it directly caused that particular production miss. It is the next failure the recovery path can encounter once it finds a section-36 anchor.

### Changes to make

**A. Repair neighbour selection first.**

- File: `backend/app/modules/retrieval/repositories/retrieval_chunk_repository.py`, `adjacent_ids()`.
- Prioritize immediate chunk neighbours in the same document/version; then same-section continuations; then physically adjacent pages where page provenance is meaningful.
- Do not use non-null `page_number` alone to distinguish paged PDFs from Markdown/text. Existing `strategy_used`, source format and heading metadata provide compatibility signals; consider an explicit page-provenance field for future ingestion.
- Rank by structural relationship and distance before capping. UUID may break ties, but must not define proximity.
- Preserve project, active-build membership, document version, anchor exclusion and bounded fan-out. Allocate candidates fairly across multiple anchors.
- Preserve page-based expansion for tables whose chunks are not consecutive in page order.
- Keep normal source-policy, relevance and authority admission after discovery. Structural proximity is not proof of support.
- Audit `services/search_service.py:190` for empty-neighbour handling: an empty restriction must not silently become unrestricted search.
- Do not reprocess the corpus as the first fix. Existing local rows already contain the content and useful structural metadata.

**B. Add one bounded missing-rule opportunity before partial acceptance.**

- Files: `services/evidence_repair_service.py`, `prompts/authoritative_compatibility.py`.
- Associate discovery attempts with stable requirement IDs. Separate annual return, AGM, accounting and audit appointment searches.
- When a required rule has no evidence and no focused attempt yet, use the existing bounded follow-up capacity before accepting a partial answer.
- Use indexed headings and observed source vocabulary for aliases. Never invent a section number from model memory.
- Retain already confirmed evidence, snapshot/filter constraints, timeout limits, duplicate-query suppression and unchanged-evidence stopping.
- For an evidenced but truncated rule, use structural neighbour recovery instead of repeatedly paraphrasing the same search.
- After the retry budget is exhausted, return an honest partial answer. Broad coverage must not cause unlimited retrieval loops.

**C. Make requirement progress observable.**

- Record, per requirement: attempted route, query, anchor, discovered IDs, admitted IDs, review-context IDs, proof IDs and precise removal/stop reason.
- Separate full-coverage validation, range validity and partial-scope validation in diagnostics.
- Keep text redaction when candidate tracing is disabled; IDs, counts and reason codes can still explain transitions.
- Update `services/chat_service.py` and operator inspection contracts where these diagnostics are serialized.

## 4. Authority: metadata audit and status semantics

### What the production flag actually establishes

The production `current_authority.records` describe tax-law relationships. Two records have `ungoverned_or_incomplete_metadata`; three applicable expansion/already-recalled records lack provision scope. `hybrid_retriever.py:_expansion_diagnostics` gives unresolved relationships precedence over the unscoped status.

The Companies Act citations have `authority_status=null`; their claims report `not_assessed`. Their empty `source_relationships` arrays are not what generated the global failure label.

`backend/app/modules/knowledge/source_metadata_read.py:449` groups citation relationships by `source_revision_id`. These are outgoing edges. An amendment normally points toward the base revision; the base's outgoing array can remain empty.

Local read-only inspection found no incoming or outgoing company-law relationships. The active local source also has no effective-from date. Its global authority status is `not_applicable`, demonstrating that absence of discovered relationships is not a certification of legal currency.

### Changes to make

1. In the authenticated source console, inspect the active Companies Act revision, its source lineage and **incoming** relationships. Determine which amendments are already incorporated in the statutory component.
2. Record documentary support for effective dates and affected provisions. Add missing MODIFIES relationships only for genuinely applicable separate amendments; do not apply an already consolidated amendment twice or invent edges to eliminate an empty array.
3. Audit tax modifiers separately. Correct operative dates and provision scopes only after source review. Rejected stale revisions should remain rejected.
4. Use immutable metadata revisions and activation through existing source services, not direct database UPDATE statements. Record the new source generation. Content/index rebuilds are needed only if actual content or indexing inputs change.
5. Keep global expansion health distinct from per-document/per-provision authority assessment. Report `not_assessed` explicitly; do not turn absent edges into `resolved`.
6. Review `current_authority.py`, `source_metadata_read.py`, `hybrid_retriever.py`, `citation_snapshots.py` and the message schema together. If adding incoming-edge display, make edge direction explicit.

Required acceptance distinction: an irrelevant tax expansion warning must not be presented as a Companies Act relationship failure, while an applicable unresolved amendment must still prevent a falsely verified legal claim.

## 5. Verification: preserve context and separate assertion types

### 5.1 Factual false negatives

Production claims 15 and 16 say “purchases and sales” and “assets and liabilities.” Both cite passage 2 and are unverified. Claim 21's penalty wording is also present in that passage.

The 2,624-character section-181 evidence was reconstructed from the source artifact and matched the API SHA-256 exactly:

`6404eee60c8010e4ac9e5c1ad360bdfa659c5aeeb4e904bcb1847c6f9e085ce1`

Local helper execution confirmed `_uses_lexical_verification=False` and no missing duration for all three claims. The code embeds individual claims and whole cited chunks; English-to-Bangla cases rely on semantic similarity. It does not perform entailment verification. Production per-claim scores and rule reasons are not persisted, so an exact numeric score cannot be reconstructed from the API response alone.

The short authority labels in claims 11 and 41 are another likely context-loss case. A label such as “Registrar” should be understood within its obligation section, not as an isolated legal proposition.

### 5.2 Scope statements are not uncited legal rules

Production claims 64–71 are bullet fragments governed by “The available materials do not establish.” The splitter discards that relationship, classifies them as `source_assertion`, finds no citation and returns `unsupported`.

`_is_insufficiency_statement()` recognizes only `not enough indexed evidence`. `_claim_kind()` does not model validated coverage limitations. `ChatService` calls `map_claims()` with the answer, selected chunks and user input, without the structured coverage verdict.

Some scope prose can itself be wrong: “the corpus contains no annual-return provision” is not established by failure to select it. The fix must distinguish a bounded statement about reviewed evidence from an unsupported claim about the entire corpus.

### Changes to make

**Claim representation and classification**

- In `grounding_service.py`, preserve heading, table-row and list-preamble context during segmentation. Retain the original display text and a contextualized assertion used for verification.
- Do not borrow citations across unrelated table rows or headings. Existing citation-boundary tests remain mandatory.
- Introduce an explicit coverage/scope statement category validated against the successful structured coverage/partial-answer result. Pass that trusted result from `services/chat_service.py` to the verifier.
- Validate scope statements before deciding whether they belong in the factual-claim denominator. Do not mark them source-supported merely because they contain phrases such as “not established.”
- Keep actual assertions inside caveats subject to verification. The production conclusion that inactivity does not eliminate obligations still needs source support.

**Factual evidence verification**

- Locate candidate supporting sentences or spans within already cited admitted evidence; preserve offsets and span hashes.
- Check the contextualized claim against those spans in both English/Bangla directions. Use a bounded multilingual entailment step for unresolved cases if deterministic checks cannot settle them; embedding similarity should select candidates, not certify truth by itself.
- Preserve quantity, deadline, negation, entity scope, conditions, arithmetic and authority checks. Matching a number elsewhere in a long chunk must not prove the same number for the claimed duty.
- Batch ambiguous cases and cache by claim/evidence/config identity to limit latency. Record provider failures as unverified with a reason.
- Expose verification method, supporting span, score where relevant, and reason code. Persist the distinction between unsupported, unresolved and merely unscored.

**Result and UI semantics**

- Keep factual grounding and question completeness separate. A partial answer can contain only supported factual claims; it must still display missing coverage.
- Specify how invalid coverage statements affect overall trust. Do not hide them by dropping all non-factual categories from reporting.
- Preserve `grounded=null` for existing no-verifiable-claim cases where appropriate.
- Review `schemas/message.py`, `services/chat_service.py`, `frontend/src/features/lab/TestLab.tsx` and `EvidenceSummary.tsx`; regenerate OpenAPI types if the API contract changes.

### Genuine errors that must remain rejected

Local message `cb2b102c-83c7-4eb1-910d-de41a917684b`, claim 30, says accounting records are retained for **11 years**. The source says **বার বৎসর**, twelve years. This is a generation error; do not tune verification to accept it.

The section-181 selected production chunk also ends inside subsection (7). It supports the quoted general penalty but is not a complete account of all liable-person categories and exclusions. Expand source context before attempting a comprehensive liability summary.

## 6. Existing baseline tests and uncovered behavior

Executed without implementation changes:

```powershell
backend/.venv/Scripts/python.exe -B -m pytest `
  tests/unit/modules/conversations/test_grounding_service.py `
  tests/unit/modules/conversations/test_evidence_repair_service.py `
  tests/unit/modules/conversations/test_current_authority.py `
  tests/unit/modules/conversations/test_retrieval_adapter.py `
  -q -p no:cacheprovider
```

Result: **234 passed, 1 failed**. No integration suite or paid-provider replay was run during this investigation.

Failure: `test_en_unrelated_claim_to_bn_evidence_is_unsupported`, at `test_grounding_service.py:1026`. The unrelated vacation claim contains “twenty days”; the earlier missing-duration guard returns `unverified` before the unrelated semantic evidence can return `unsupported`.

Resolve this explicitly before declaring the baseline green: either accumulate reasons and give reliable contradiction/unrelated-evidence rejection precedence, or document a deliberate status contract and adjust the expectation accordingly. Do not weaken rejection of invented durations merely to satisfy a status assertion.

Existing `tests/unit/modules/retrieval/test_adjacent_chunk_scope.py` checks compiled SQL and scope constraints using a mocked session. It does not execute a synthetic-page corpus or assert that the immediate predecessor survives the limit. That is why the demonstrated adjacency bug needs a real repository-result test.

## 7. Regression tests to add during implementation

| Area | Required scenario | Expected result |
| --- | --- | --- |
| Adjacent repository | More than 48 Markdown chunks on synthetic page 1; anchor 49, required predecessor 48, shuffled UUID ordering | Chunk 48 is returned before unrelated sections |
| Adjacent repository | Multiple anchors from different sections | Fair bounded allocation; no anchor starves the others |
| Adjacent repository | Real PDF table spanning neighbouring pages with nonconsecutive indices | Correct page neighbours remain discoverable |
| Isolation | Other project/build/document version, inactive anchor, empty anchor list | No scope escape or unrestricted fallback |
| Recovery | R1 absent but other requirements have valid partial evidence | One focused missing-rule opportunity, then bounded partial result if still absent |
| Recovery | Supported R1 fragment still requests continuation | Fetch structurally relevant continuation before acceptance |
| Recovery | No new evidence, duplicate query, timeout or snapshot change | Stop safely with explicit reason; preserve confirmed proof |
| Coverage | Annual duty established, separate forms/details missing | Narrow duty supported; only unresolved details remain pending |
| Proof handoff | Both section-36 applicability and operative deadline required | Both necessary spans survive review and generation context |
| Claims | English purchases/sales and assets/liabilities against exact Bangla span | Supported, with stable source attribution |
| Claims | Short authority cell/label under an obligation | Context retained; no unrelated citation inheritance |
| Claims | Negated missing-evidence preamble with several bullets | Validated scope statements, not unsupported legal duties |
| Claims | “No provision exists anywhere” after partial search | Not silently exempted or treated as proven |
| Claims | Twelve-year source, eleven-year answer; altered deadlines/penalties | Not supported |
| Claims | Valid citation but unrelated assertion; genuine negation/exception mismatch | Not supported |
| Claims | Embedding/entailment provider unavailable | Unverified with explicit failure reason |
| Authority | Base has no outgoing edges but applicable incoming modifier | Incoming edge is assessed correctly |
| Authority | Amendment already incorporated, unrelated tax modifier, scoped disjoint amendment | No double application or irrelevant demotion |
| Authority | Missing operative date/scope, modifier excluded from active context | Limitation remains visible; no false resolved status |
| UI/API | Supported partial answer, factual failure, invalid coverage statement, unassessed authority | Distinct labels and accurate counts; snapshot compatibility |

Extend these existing suites:

- `tests/unit/modules/retrieval/test_adjacent_chunk_scope.py`
- `tests/unit/modules/conversations/test_evidence_repair_service.py`
- `tests/unit/modules/conversations/test_grounding_service.py`
- `tests/unit/modules/conversations/test_current_authority.py`
- `tests/unit/modules/conversations/test_citation_snapshots.py`
- `tests/unit/modules/conversations/test_retrieval_adapter.py`
- `tests/integration/test_phase3_source_retrieval.py`
- `tests/integration/test_source_relationships.py`
- `frontend/src/features/lab/MessageInspector.test.tsx`
- `frontend/src/features/lab/TestLab.test.tsx`

Use deterministic fake providers for control-flow and verdict-contract tests. Those tests do not establish multilingual model quality; keep a separate human-reviewed bilingual fixture and provider-backed evaluation for that.

## 8. Local execution and verification plan

### Step 1: freeze the baseline

Record checkout SHA, loaded server version, project and conversation snapshot IDs, provider/model/calibration versions, source generation, active build and effective configuration. A saved conversation can retain older configuration; create a fresh test conversation when comparing a new configuration.

Use Local Test for company-law isolation. Use a separate production-shaped local fixture for NBR/Finance Act authority and coverage work. Preserve missing-tax expectations in the small local corpus.

The observed frontend is on 5173 and proxies the API, but port 8000 was not listening directly. Check the actual dev proxy target; do not assume restarting an arbitrary port changes the serving backend. For a new production-like local stack, follow `CONTRIBUTING.md` and the compose configuration; Docker must be running. No stack restart was performed here.

### Step 2: reproduce neighbour discovery without generation

Run the real repository method under a read-only transaction with these inputs:

```text
project_id: 6cd8b5f4-7273-4b10-be84-064c130c8303
index_build_id: abb7e951-5d66-4c40-953c-be887564895d
anchor_ids: [17cd3c12-8935-477b-a03a-a2d0180564d1]
required neighbour: 424bed51-cc56-4bc2-aa2c-d711caea1190
```

Before: count 48, required neighbour absent. After: required neighbour present with a structural proximity reason; bounds and isolation preserved. If the corpus is reprocessed, resolve IDs by document/section/content hash rather than hard-coding these historical UUIDs.

### Step 3: diagnostic retrieval matrix

In Operator Console Test Lab/search, or its authenticated project search API, compare:

1. Original broad inactive-company question.
2. `Bangladesh Companies Act annual return AGM` (production baseline route).
3. `Companies Act annual list summary private company`.
4. `সদস্যগণের বার্ষিক তালিকা সার-সংক্ষেপ`.
5. `কোম্পানির বার্ষিক বিবরণী রেজিষ্ট্রার`.
6. A section-36 focused query, once the section mapping is established from the source.

For each, capture raw candidate/rerank/admission/review/proof transitions, source generation and build. Start with identical baseline configuration. Only then compare document-scoped or translated variants, changing one factor at a time. A document-scoped success diagnoses recall competition; it is not proof that broad project retrieval is fixed.

### Step 4: answer-level cases

- Focused English table for sections 36, 81 and 190, preserving exceptions and deadlines.
- Bangla section-36 response, including the private-company certificate condition.
- Original broad question without provision-number hints.
- Follow-up: shorten the answer into three Bangla bullets, preserving current-turn citations.
- Focused section-181 records question; verify twelve years and the correct penalty wording.
- Deliberately altered numeric/negated claims in offline fixtures, which must fail.

Inspect proof text, not just a green badge or expected substring. For each answer record: completeness, factual support, scope-statement validity, authority status, number of provider calls, recovery rounds, token use and end-to-end latency. Repeat the representative provider-backed cases three times after deterministic tests pass; report all results rather than the best run.

### Step 5: run implementation gates

Run targeted unit suites first, including the new neighbour tests. Then run backend unit/architecture and evaluation smoke tests:

```powershell
backend/.venv/Scripts/python.exe -m pytest tests/unit tests/architecture
backend/.venv/Scripts/python.exe -m pytest tests/evaluation -m evaluation_smoke
backend/.venv/Scripts/python.exe -m ruff check backend tests
backend/.venv/Scripts/python.exe -m ruff format --check backend tests
backend/.venv/Scripts/python.exe -m mypy backend/app
backend/.venv/Scripts/python.exe scripts/check_migrations.py
```

Repository tooling requires Python 3.12. Follow `CONTRIBUTING.md`; these are future verification commands, not claims that all gates were executed for this document.

Integration tests migrate a database. Use an explicitly disposable database, never the Local Test backing database or production:

```powershell
$env:APE_DATABASE__NAME = 'ape_test'
$env:APE_TEST_DATABASE__NAME = 'ape_test'
$env:APE_TEST_DATABASE__ALLOW_MIGRATIONS = 'true'
backend/.venv/Scripts/python.exe -m pytest tests/integration
```

Verify host/port and disposable-database guards before running; use a dedicated test shell and close it afterward. Skipped database tests do not count as a pass.

If message/diagnostic contracts or console rendering change, run from `frontend`:

```powershell
pnpm api:generate
pnpm format:check
pnpm lint
pnpm typecheck
pnpm test
pnpm build
```

With Make available, the repository's aggregate gate is `make quality`; run `make frontend-build` as well. Review generated-file diffs and any schema migration independently.

### Step 6: authenticated console acceptance

After browser access works, inspect both the answer and its detailed trace:

- Source inventory: active revision, original source text, section-36 split, incoming/outgoing relationships and current build.
- Conversation settings: effective snapshot matches the intended test configuration.
- Claim inspector: supporting span and failure reason are understandable; scope statements are displayed separately.
- Partial answer: missing topics remain visible even if every factual claim is supported.
- Authority display: unrelated tax expansion problems are not attributed to company-law passages.
- Follow-up citations: citation numbers resolve to the correct current answer sources.

This UI acceptance remains pending; backing-data verification does not replace it.

## 9. Final acceptance and release checklist

Do not call this fixed solely because `grounded=true` or the annual-return phrase appears.

- [ ] Indexed section-36 opening and continuation are discoverable with correct structural proximity.
- [ ] Broad annual-return recovery is demonstrated without hard-coded section hints.
- [ ] Missing-rule retries are bounded and explain why they stop.
- [ ] Valid partial proofs survive recovery and final selection; no whole-corpus absence claim is inferred from selected-context absence.
- [ ] Genuine factual errors, including the eleven-year example, remain rejected.
- [ ] Supported bilingual fragments are accepted with evidence spans; invalid scope statements are not exempted.
- [ ] Authority metadata audit has documentary justification; empty outgoing edges are not treated as proof of missing incoming edges.
- [ ] Tax regressions pass on a production-shaped corpus with frozen configuration and source identities.
- [ ] Existing failing unit case is resolved under an explicit verification-status contract.
- [ ] Targeted, integration, architecture, evaluation and frontend gates pass without silently skipped required checks.
- [ ] Provider-backed repeated runs and latency/token results are attached, with remaining limitations stated.
- [ ] Authenticated operator UI acceptance is complete.

Implement in reviewable increments: structural neighbours; recovery orchestration; claim/scope verification; metadata revisions and reporting. Keep code changes separate from source metadata changes so regressions can be attributed correctly. Preserve prior code/configuration/source revision identities for rollback. After a later deployment, repeat the production-shaped smoke cases and verify the loaded code and source generation before declaring completion.

## 10. Navigation references

All code paths below are relative to repository root `E:/python-projects/rag-builder`; line anchors describe the inspected checkout and can shift after implementation.

| Purpose | Location |
| --- | --- |
| Annual-return source | `artifacts/company-readiness/Companies Act 1994 - STATUTORY SECTIONS - Official Bangla.md:159` |
| Accounting source | Same file, section 181 at approximately line 741 |
| Neighbour candidate bug | `backend/app/modules/retrieval/repositories/retrieval_chunk_repository.py:20` |
| Adjacent candidate restriction | `backend/app/modules/retrieval/services/search_service.py:190` |
| Planning instructions | `backend/app/modules/conversations/prompts/authoritative_compatibility.py:8` |
| Partial acceptance and continuations | `backend/app/modules/conversations/services/evidence_repair_service.py:1050` |
| Final proof handoff | Same file, approximately line 1226 |
| Coverage/range validation | `backend/app/modules/conversations/services/evidence_coverage.py:125` |
| Outgoing relationship projection | `backend/app/modules/knowledge/source_metadata_read.py:449` |
| Global authority aggregation | `backend/app/modules/retrieval/retrievers/hybrid_retriever.py:890` |
| Per-passage authority limitations | `backend/app/modules/conversations/current_authority.py` |
| Claim segmentation/verification | `backend/app/modules/conversations/grounding_service.py:804` |
| Whole-chunk embedding comparison | Same file, approximately line 1016 |
| Scope detection limitation | Same file, approximately line 2141 |
| Verifier call site | `backend/app/modules/conversations/services/chat_service.py:1120` |
| Claim API contract | `backend/app/modules/conversations/schemas/message.py:182` |
| Operator claim display | `frontend/src/features/lab/TestLab.tsx:2663` |
| Local setup and gates | `CONTRIBUTING.md`, `Makefile`, `tests/conftest.py` |

The prior investigation's main conclusions remain valid, with one important refinement: structural neighbour retrieval is now a directly reproduced defect, and not every unverified local claim is a false negative.
