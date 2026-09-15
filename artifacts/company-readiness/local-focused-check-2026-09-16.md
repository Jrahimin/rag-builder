# Focused local business conversation check

Project: `6cd8b5f4-7273-4b10-be84-064c130c8303` (Local Test). Baseline code: `2f68bcf`.
Production sources and policy were not changed.

## Latest verification: previously unresolved company cases repaired

Follow-up conversation `19fb0889-2d35-4280-8b53-74f3f4a727e6` ran two affected cases after implementation:

| Case | Result | Time |
|---|---|---|
| Original English sections 36/81/190 deadline table, without workaround wording | Complete coverage; recovered; one adjacent-context round; 11/11 supported claims; English response | 87,860 ms |
| Bangla section 36 deadline and certificates, explicitly including more than 50 members | Three cited bullets; 3/3 supported claims; grounded; separate coverage not assessed | 26,993 ms |

The certificate flag was a deterministic numeric-format mismatch: the source spells `পঞ্চাশ`, while answers use `50` or `৫০`. The comparison guard now accepts isolated word-number equivalents before applying the existing lexical/semantic checks. It still rejects changed counts, does not extract fifty from one hundred fifty or দুইশত পঞ্চাশ, and excludes ambiguous standalone বার from count normalization. Ten targeted grounding cases and lint passed. These two messages close the specific context/claim gaps reported in the prior run; the historical results below remain unchanged for auditability.

The Bangla answer's employee exception was also checked against the local official-text section 2(1)(ট)(ই), rather than relying solely on its green verification status. No changes were made to the three-source corpus, active policy or thresholds. Broad-query latency and available-versus-used citation presentation remain separate limitations; the small corpus still cannot certify current RJSC procedures or missing schedules.

## Corpus

Three sources uploaded, all confirmed Ready / primary / active:

- BIDA Business and Investment FAQ — `BIDA business FAQ.md`; document `d39121c1-df13-422a-ab4d-34c67118e472`; work `bd.business.bida-faq`; `official_guidance`; native English text.
- Sale of Goods Act 1930 — Bangladesh — `The Sale of Goods Act, 1930.md`; document `2443c285-7f67-400c-acc2-75281645b7df`; work `bd.business.sale-of-goods-act-1930`; `statute`; native English text.
- Companies Act 1994 — Official Bangla Statutory Sections — document `f8d740bd-28eb-4818-a251-bb2b3dd5c5f8`; work `bd.companies-act-1994`; `statute`; parser `plain_text`, detected language `bn`. Official title কোম্পানী আইন, ১৯৯৪ retained in text and change reason. Previously audited official-text artifact used instead of incomplete English PDF.

These are independent works, with no amendment/replacement links. Dates were left unset rather than inventing consolidated validity dates. The Companies Act file covers statutory sections, not schedules. Existing unrelated local documents were preserved. Source generation: 4.

## Configuration decisions

Revision 2 (`c0717a91`): replaced inherited tax-only domain instructions with source-based business guidance; indexed-only response mode; query translation on; Quality retrieval. Authoritative evidence approach and balanced grounding retained.

Initial test conversation `0cae1262-f791-453b-8560-0e256d467990`: sections 36/81/190 deadline table. 58,538 ms; three cited passages; 9/9 supported claims; coverage partial. Table and citation buttons rendered semantically. Reviewer reported truncated conditions for all three provisions. Six recovery searches still selected short passages; partial-answer acceptance ended recovery. Runtime: Cohere embed-v4.0, rerank-v4.0-pro, GPT-5.6 Luna, 8,192 output reserve. Token capacity was not exhausted.

Revision 3 (`9ff1dd21`): local Custom profile based on Quality. Expanded passage window from 128 to 512 and context character budget from 16,000 to 32,000. Kept candidates 80/80, rerank window 40, top K 12, context chunks 10, per-document cap 6 and relevance/authority thresholds. This addresses observed passage truncation without changing source admission.

Verification conversation: `46b58bb2-7670-4d3f-b97e-a2afd05d2166`: 83,616 ms, two citations, 8/12 supported claims. Broader passages established sections 81 and 190 conditions; section 36 scope still lacked its preceding chunk. Four unsupported items were statements about missing evidence. This configuration alone did not solve the question.

Conversation `a43f8cc4-9d23-46af-a284-23de0eecda88`: repeated company question returned in 58,961 ms with two citations, partial coverage and 8/14 supported claims. It incorrectly answered an English question in Bangla. Section 36 remained unresolved and the planner still misassociated section numbers. No claim that the new Python code was loaded is made: the local process restart/reload status could not be established.

## Targeted implementation

The recovery service previously accepted any valid partial answer before considering the existing adjacent-passage route. It now defers that early acceptance on the first round only when an unsupported check requests adjacent context, has validated source ranges and a retrievable anchor. Existing source, project, period, reranking and admission checks remain in place. Ordinary partial answers retain their early exit. Authoritative planning now prohibits guessing topic-to-section associations from an unordered list; coverage instructions explicitly recognize same-page chunk continuations. Diagnostics identify the change as `v20-bounded-continuation-before-partial` and expose the continuation flag.

Six focused backend cases passed, covering ordinary partial scopes and adjacent recovery with/without a valid partial answer. Ruff and diff checks passed. No integration suite was run.

The subsequent Bangla Sale of Goods test **confirmed v20 running locally** and exercised adjacent recovery; no API restart is needed. It returned a useful comparison and remedies with 14/16 supported claims, but remained partial. Its trace exposed an additional issue: the adjacent query used the first missing description instead of the requirement attached to its source anchor. The service now uses that check's description, with legacy fallback only when absent. The targeted tests verify this association even when another missing topic is listed first; all six cases pass.

The BIDA English question returned in 29,955 ms: commercial/trading registration distinction answered, 2/3 supported claims. The uncited uncertainty sentence remained flagged. Its API evidence list contained six passages although the prose cited only passage 1; this is a remaining presentation/metrics distinction, not evidence that six sources supported the answer.

Final company verification conversation: `2474a3bf-0afb-4a69-81da-95a2e7c08a9d`.

The final broad company question took 74,702 ms, with 10/14 supported claims and partial coverage. Its planner now correctly maps section 81 to AGM and section 36 to annual return. The reviewer marked section 36 supported **and** requested adjacent context for applicability; recovery had incorrectly ignored supported anchors. Both continuation predicates now honor the explicit flag on an incomplete review regardless of the supported value. Eight focused cases pass, including both anchor states, partial/nonpartial reviews, correctly associated queries and preserved project/document/date filters.

A focused section 36 follow-up answered the 21-day filing period and private-company certification requirements with 5/6 supported claims; the more-than-50-members certificate sentence remains unverified. This confirms that the statutory content exists and is retrievable. It does not prove the original broad question is fully repaired. The returned citation inventory again includes unused context passages (ten versus one inline reference).

The final Bangla rewrite returned three short bullets with citations in 34,780 ms. Coverage was complete and recovery found section 36 chunk 48 (`424bed51-cc56-4bc2-aa2c-d711caea1190`) together with chunk 49 (`17cd3c12-8935-477b-a03a-a2d0180564d1`). Claim verification was 2/3: the more-than-50-members certificate sentence remained unverified. Presentation and conversational context retention worked; this is not a fully passing grounding result.

## Bounded-run outcome

Eight local messages in total, including diagnostic repetitions, cross-domain guidance, Bangla commercial law and a rewrite. Eight focused backend test cases passed after the final implementation; lint and diff checks passed. No full integration suite was run. The source/configuration changes are active locally and the new recovery version was observed in API diagnostics. The final supported-anchor change is covered by focused tests; the original broad company query was not repeated again after it, avoiding another tuning loop.

Remaining priorities are broad-question evidence continuity, calibrated verification of caveats and the private-company certificate statement, stable response-language selection, distinguishing used citations from available evidence, and reducing multi-stage latency. No blanket token/provider increase is justified by this run. The local fixture is ready for focused development; company-law readiness is not signed off.

## Scope limits

This small corpus is for local diagnosis, not a complete company-incorporation knowledge base. Current RJSC fees/forms/procedures, Companies Act schedules and comprehensive licensing coverage remain outside the experiment. No broad integration suite is planned.
