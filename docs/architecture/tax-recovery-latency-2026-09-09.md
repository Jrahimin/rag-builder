# Tax recovery: deployed acceptance and latency follow-up

The deployed v17 partial-answer path is confirmed. Runtime optimizations are implemented,
tested, and verified in one final fresh local smoke after restart. Embedding calls fell
from 20 to 16, but total response time increased from 102.762 to 107.057 seconds because
this run needed another recovery round. No overall latency improvement is established.
No additional live smoke was sent.

## Fresh original-scenario comparison

One request per baseline environment and one final local request, each in a new Operator
Console conversation. All used the verbatim
`gross_with_interest` question in `scripts/evaluate_rag_enhancements.py`: salary 1,200,000;
rebateable investment 60,000; Sanchaypatra interest 24,000; bank interest 2,200; male below
40; Chittagong; latest financial year. No extra instruction solicited a partial answer.

| Run | Browser round trip | Backend processing | Recovery | Generation | LLM / rerank / embedding calls | LLM input / output tokens |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| Earlier supplied local partial run | 129.364 s | Not supplied | 95.242 s | 14.839 s | 5 / 11 / 24 | 95,122 combined |
| Deployed live, this session | 47.582 s | 45.408 s | 28.439 s | 12.965 s | 3 / 9 / 20 | 47,762 / 3,350 |
| Local before changes, this session | 102.762 s | 101.558 s | 65.870 s | 25.253 s | 3 / 9 / 20 | 50,690 / 6,681 |
| Local after changes | 107.057 s | 106.046 s | 84.248 s | 12.733 s | 5 / 11 / 16 | 87,877 / 7,351 |

Live conversation: `495233b1-d4d4-4377-9c02-ee5ed0e81e76`, project revision 9, source
generation 37, active build prefix `adca16b6`. Local conversation:
`207d1a88-264d-4c13-8e1c-d6606324b93e`, revision 36, source generation 22, build prefix
`bc61c185`. Both report authoritative evidence, translation off, `gpt-5.6-luna`,
`rerank-v4.0-pro`, `embed-v4.0`, and `v17-authoritative-partial`.

These are different corpora/configuration snapshots, not a controlled speed comparison.
The earlier local prompt also explicitly requested partial work; it was not a verbatim
baseline. Stage durations overlap. Embedding token usage and provider-internal retries
are unreported, not zero. LLM reasoning tokens are reported separately and are not added
again to output totals: live 1,536; local baseline 4,274; final local 4,653.

## Evidence and answer outcome

Both baseline runs showed **Partial answer / Needs Attention**, coverage `partial`, claim
verification `unverified`, and explicitly withheld complete mixed-income liability.
Live returned salary income after exclusion of BDT 800,000 and an estimated BDT 6,000
rebate. Local returned BDT 800,000 taxable salary, BDT 45,000 salary-only tax before
rebate, and a BDT 6,000 investment-based ceiling, leaving the final rebate unresolved.
Claim support was 9/36 live and 20/39 local. Arithmetic consistency is not certification
of the provisions, applicability, or separability of a subtotal.

Live exact proof identities/ranges recorded in recovery diagnostics:

| Selected passage | Reviewer ranges | Supported scope |
| --- | --- | --- |
| Nirdeshika 2026–27 p28, chunk 41, `f40289ee-55f7-4a84-93d5-7c4bfab68171` | L7–25 | Employment exclusion |
| Paripatra 2026–27 p74, chunk 95, `f7a3a81c-68cf-4fba-a17a-1c4443c4fabe` | L1–27 | Rebate rule |
| Nirdeshika 2026–27 p135, chunk 280, `751efbaa-d781-4a5b-8478-af1703acf575` | L33–39, L47–58 | Additional rebate proof; excerpt is a return-form passage |

Live rejected threshold/rate completeness (R2), and interest inclusion/final-tax
completeness (R4). R4 inspected `5bc3e389` L5–10, `81932486` L15–23, and the known
Ordinance 52 passage `15224a75` L7–27. That ordinance was found and admitted by the
English interest search; finding it did not establish the current rule chain.
`source_ranges_validated=true` establishes exact selectors; whole-question
`quotes_validated=false` correctly remains false for the incomplete verdict.

Local selected six proof IDs: `0cc3245d` (Nirdeshika p25), `2ff2b04c` (Nirdeshika p28),
`84830e90` (Finance Act p140), `fe29a170` (Paripatra p74), `56e4c651` (Finance Act p143),
and `e203a865`. It left Sanchaypatra aggregation/final withholding, bank-interest
aggregation/source tax, and complete minimum-tax comparison unresolved. Both environments
still report two unscoped modifier relationships and one incomplete-metadata relationship;
live also reports two stale relationships, local three.

This inspection covered diagnostics, exact proof selectors, and visible citation excerpts.
It did **not** authenticate full source documents or finish the section 163 commencement
and replacement audit. Full liability is not accepted. The live rebate independence
claim still needs legal review. Local also emitted an inappropriate literal
`[Developer scope limitation]` marker and discussed a minimum-tax branch outside its
selected partial requirement IDs, apparently using a passage shared with another selected
requirement. These are remaining generation-scope limitations; the latency changes do not
claim to solve them.

## Why work repeats, and implemented changes

The current planner requires paired language routes for each concept, yielding eight
distinct searches. Both baseline runs stopped at the **first** coverage review, unlike the
earlier ten-search/two-review run. Nine reranks correspond to the initial query plus
eight distinct recovery queries: they are not identical requests eligible for safe score
reuse. Existing exact embedding caching already saved 139 text requests live and 189
local. Local LLM calls alone took 20.482/22.962/25.253 seconds, compared with
7.736/9.724/12.965 live. Local database vector stages totalled 19.117 seconds versus
4.641 live, with overlapping branch timings.

Implemented without changing model settings or reducing evidence budgets:

1. Batch planned recovery query embeddings through the already-resolved, identity-scoped,
   turn-local cache. Eight distinct queries can use one provider batch instead of eight
   serialized calls. Each branch still resolves its pinned snapshot, searches, reranks,
   reconciles authority, and performs admission. Provider failures remain failures.
2. Stop a repeated coverage review when the exact passage text/identities, source authority
   metadata, authority records and requirements are unchanged. Search wording and rank
   alone cannot turn an unsuccessful review into new proof. This stop preserves refusal;
   it never promotes completeness or invents a partial scope. Changed text, effective
   dates, revisions, authority records or requirements still require review. Legacy
   route-based coverage is not deduplicated.
3. Correct a stale test expectation: an explicitly false arithmetic equation is
   `unsupported`, not merely `unverified`. No arithmetic implementation was changed.

The batching test demonstrates call reduction with all searches retained. The final
smoke confirms batching, but does not demonstrate an end-to-end speedup. The new
`recovery_query_embedding_batches` counter confirms that runtime loaded the change;
`coverage_reviews_skipped` and `stop_reason=unchanged_review_evidence` expose the stop.

## Final local acceptance

Conversation `438294ec-3262-4299-975f-a4bdb5d48c7f` used the original Income Tax & Budget
project, revision 36 and source generation 22, with the same model settings. It returned
**Partial answer / Needs Attention**, coverage `partial`, claim verification `unverified`
(6/36 supported). It supplied the rebate formula and BDT 6,000 investment ceiling,
conditional on applicable income and eligibility; it did not establish a final rebate
or complete liability. It explicitly excluded salary and both interest components rather
than treating them as zero. Unlike the baseline, it did not return the salary subtotal.

Selected exact proof ranges were validated, while whole-question `quotes_validated`
remained false:

| Selected passage | Reviewer ranges | Supported scope |
| --- | --- | --- |
| Nirdeshika p134, chunk 279, `78f3b8c8-0bcb-407e-b57e-d44293df8078` | L47–55 | Period mapping (return-form/portal excerpt) |
| Nirdeshika p60, chunk 105, `bcf7e154-bab5-45a9-9eba-24c3e6209122` | L7–21, L35–50 | Rebate |
| Paripatra p74, chunk 96, `fe29a170-ed8f-4abb-9d09-9d40ba1ddfe0` | L19–27 | Rebate |

Pending gaps were schedule commencement/applicability, salary transformation and interest
aggregation, gross/net interest and source-credit/final-tax treatment, and minimum tax.
Recovery still demanded a Chattogram-specific minimum-tax rule. The smaller partial scope
and period mapping from a form excerpt remain review limitations; this is no legal
certification. Authority diagnostics still showed two unscoped, one incomplete-metadata,
and three stale relationships.

Eight initial searches plus two focused searches produced two coverage reviews. The
focused queries were `চাকরি হইতে আয় নিরূপণ` and `"বিনিয়োগজনিত কর রেয়াত পরিগণনা"`.
`recovery_query_embedding_batches=2` confirms the loaded change: eight planned queries
and two focused queries used two batches, saving eight provider calls compared with
embedding those same ten queries individually. Across the whole run there were 276
embedded texts, 203 embedding-cache hits and 617 content-cache hits. Query embedding
time was 1.974 seconds versus 4.599 in the baseline; overlapping stages and changed
recovery work prevent assigning a controlled wall-clock saving to this difference.

The five LLM calls took 18.601/16.467/5.790/20.170/12.734 seconds (initial planning,
coverage, focused planning, coverage, generation). Total LLM usage was 95,228 tokens.
Recovery rose by 18.378 seconds; overall round trip rose by 4.295 seconds (4.2%).
The follow-up changed evidence, so the unchanged-review stop was correctly not exercised;
that behavior is covered by targeted tests. Distinct searches/reranks and necessary
changed-evidence reviews remain. No further smoke was sent.

## Validation and remaining acceptance

Backend conversation/provider/architecture suite: **492 passed**; a subsequently added
batch-warmup failure test also passes (final batch suite: 12 passed). Ruff and mypy on
changed runtime files pass. Frontend message/lab tests: **21 passed**; TypeScript and
production build and scoped ESLint pass. Restored pinned local test dependencies without
changing lockfiles. Earlier Git diff whitespace checks passed; the final report-only
recheck could not run because Git no longer recognized the workspace as a repository.

No source documents, source metadata, indexes, models, project settings or policy revisions
were changed. No migration or reindex is required. The runtime changes are local and still
need deployment to affect live latency. No specific live metadata correction has been justified:
reconcile the actual replacement section 163 and commencement with the earlier 163(11),
then applicable sections 102/105 and credit/exception provisions before editing relationships.

The final local smoke is complete. Local runtime is not Docker-based; no container or
runtime configuration change was needed. Full tax-law coverage and stable partial-scope
selection remain unresolved. Work is finalized within the requested scope, with no
additional live configuration or source changes proposed without the document audit.

## Small follow-up changes and next investigation

At the user's request, this follow-up stops at three bounded changes:

1. Focused planning now receives the descriptions and IDs of supported requirements
   whose exact quoted evidence passed the existing retention checks. Its instructions
   direct searches toward the missing transformation/applicability instead of searching
   a supported formula again. This is discovery context, not permission to skip final
   coverage or authority validation. Recovery prompt provenance advances to v22.
2. Focused queries are deduplicated against previous queries and each other using case
   and whitespace normalization. A repeated query is discarded before retrieval,
   reranking, embedding, or another coverage review. The diagnostic counter is
   `duplicate_focused_queries_skipped`. Meaningful punctuation, quoted phrases, periods,
   and alternate-language routes are preserved. No semantic similarity cutoff was added.
3. Partial generation instructions explicitly restrict shared passages to the reviewed
   requirement scope, keep pending rules as gaps rather than calculations, and prohibit
   internal instruction labels and reviewer IDs in the answer. Grounded prompt provenance
   advances to v15. This is a prompt improvement, not a deterministic output guard.

Validation was limited to the affected recovery/prompt suites: **92 tests passed**, plus
Ruff and Git whitespace checks. Tests verify that retained semantic proof reaches the
focused planner, cosmetic duplicate searches stop without promoting an answer, and the
partial prompt includes shared-passage and internal-label constraints. No broad test run,
frontend changes, provider smoke, deployment, or source/configuration mutation was made
in this follow-up. The timings above predate these three changes; no new speed claim is
made. The earlier final Git-check issue was resolved with a command-scoped safe-directory
option, without changing global Git configuration.

Prioritized next work:

| Priority | Investigation / proposed fix | Evidence and acceptance condition |
| --- | --- | --- |
| 1 | Bind generated claims to the selected requirement's exact proof ranges, including conditions and dependencies. | A whole selected passage can contain unrelated rules. The current prompt constraint needs a stronger claim-to-scope check; verify that unselected minimum-tax rules and unsupported period mappings cannot become asserted results. Preserve headings/conditions when narrowing context. |
| 2 | Audit current interest provisions and effective dates in the actual documents. | Reconcile sections 62–65, base 105, Ordinance 52's 163(11), and the later replacement section 163. Record the operative text, commencement, applicability, and remaining contradictions before proposing specific relationship corrections. Full liability stays withheld until all required proof is available. |
| 3 | Inspect whether focused planning now avoids already-supported concepts, and why salary proof is inconsistently selected. | Compare supported IDs, missing dependencies, selected ranges and searches in one future fresh smoke. The final measured run returned a narrower partial and spent 84.248 seconds in recovery. Existing location guidance is already present; another generic location instruction alone is unlikely to fix it. |
| 4 | Reduce coverage-review context using measured evidence utility. | The final two coverage calls consumed 37,048 and 38,239 input tokens. Investigate repeated or irrelevant passages and safe per-requirement context selection. Retain authority records, exact proof, temporal conditions and independent alternate routes. Do not skip a changed-evidence review simply because the preceding run was partial. |

Do not assume the earlier focused branch was unnecessary: it changed evidence, and the
current diagnostics do not prove it could safely have been skipped. Distinct reranks are
also not established duplicates. The next acceptance should measure answer scope and
provider calls together with time; fewer calls alone did not improve the last wall-clock
result. A production deployment is still needed for any local runtime/prompt changes to
affect live behavior.
