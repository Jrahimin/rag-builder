# Held-out turn-resolution evaluation

Development Journey packs are not the held-out continuity set. Do not author,
commit, or paste held-out examples in this repository until a candidate is
frozen.

## Freeze checklist

Before a later release evaluation:

1. Freeze the resolver prompt and `TURN_RESOLUTION_PROMPT_VERSION`, validation
   rules in `turn_resolution.py`, model/settings used for the candidate, and
   development fixtures (`tax_v1`, `business_conversation_v1`).
2. Have an independent evaluator or reviewer author unseen scenarios under the
   coverage rubric below. Keep exact examples outside implementation and tuning
   sessions until the candidate is frozen.
3. Lock the dataset hash, scoring rules, and denominators before execution.
4. Score every applicable turn, including fallbacks and timeouts.
5. Report results by category and total sample size. If failures lead to
   tuning, the exposed set becomes development data and a fresh held-out set is
   required.

Run an external pack with the production Journey harness. Do not copy examples
into `tests/fixtures/journeys/`:

```text
python -m app.cli rag-journey --fixture <external-held-out>/journey.json
```

Optional `--replay-raw-retrieval` may be used for measurement. It does not
change production retrieval.

## Coverage rubric

Held-out cases must include novel entities, amounts, wording, and domain
context. Cosmetic rewrites of development cases do not count. The sample
should cover:

| Category | Intent |
| -------- | ------ |
| Standalone | New questions that do not depend on history |
| Follow-up | Referential continuation of the current subject |
| Adoption | Unambiguous user instruction adopting a prior assistant value |
| Correction | Latest explicit user parameter replaces the active input |
| Ambiguity | Multiple candidate amounts, sources, or plans require clarification |
| Verification | “Was that amount correct?” does not adopt the value |
| Topic change | Old amounts and conversational dates must not carry forward |
| Language switch | English, Bangla, and code-switched history with Unicode digits |
| Temporal | Exact user or adopted dates; “before that” clarifies instead of guessing |
| Scope | Hard `document_id` / `metadata_filter` stay per-request and non-sticky |
| Comparison | Bind both source identities; old citation numbers are not reused |

## Scoring rules and denominators

Score structured resolution, not rewritten question prose.

| Metric | Numerator | Denominator |
| ------ | --------- | ----------- |
| Structured resolution accuracy | Turns whose recorded outcome, relation, active values/origins, temporal intent, and snapshot match the locked expectation | Every applicable turn, including fallbacks and timeouts |
| Clarification accuracy | Expected clarifications that returned `finish_reason=clarification` / outcome `clarify` | Expected clarification turns |
| Complete-sequence success | Sequences where every turn passed | Sequences in the locked sample |
| Fallback rate | Fallback outcomes | Attempted resolutions (bypasses excluded) |
| Scope/safety violations | Incorrect accepted bindings, sticky filters, or citation-date snapshots | Turns in the safety categories above |

Target at least **90% structured resolution accuracy** on that locked sample,
with clarification accuracy reported separately. That is a later release check
on the sample, not a universal reliability claim, and it is not demonstrated by
the development packs in this repository.

Bypasses (`casual_turn`, `no_usable_history`) are reported separately and are
not counted as attempted resolutions.

## Dataset location

Keep held-out `journey.json` files outside the repo, or under an untracked path
compatible with `tests/fixtures/journeys/held_out/` (gitignored except the
README). Do not add examples to git.
