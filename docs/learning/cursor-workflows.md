# Cursor workflows

Pick the smallest `/cur-*` command. Select the parent model in the picker, then paste a prompt. **Defaults already live in the skill.** If you want the default, do not repeat models, review counts, or repair counts.

Keep `/cur-dev` on as a Custom Mode (`Alt+Enter`) for a long run. Do not pair it with `/goal` unless you restate the review and repair counts in the goal.

## Defaults

Skip these in the prompt when they already match.

| You run | Parent picker | Reviews | Repairs | Reviewer | Who repairs |
|---|---|---:|---:|---|---|
| Substantial `/cur-dev` | Grok 4.7 **XHigh** | 2 | 2 | `cur-reviewer` · Opus 5.5 High | Parent for behavior; `cur-support` for tests/docs/lint |
| Small `/cur-dev` | Grok 4.7 **High** (standard, not Fast) | 1 | 1 | same | same |
| `/cur-verify` | Grok 4.7 High | 0 | 0 check-only, 2 if you ask for fixes | — | `cur-support` |
| `/cur-review` | any; reviewer is pinned | 1 | 0 | Opus 5.5 High | none |
| `/cur-ci` | Grok 4.7 High | 0 | 2 | — | `cur-support` |
| `/cur-qa` | Grok 4.7 High | 0 | 0 | — | none |
| `/cur-ship` | Grok 4.7 High | 0 | 0 | — | explicit commit/PR only |

Browser QA is off for small `/cur-dev` unless you ask. Substantial `/cur-dev` does one inspection and one retest when UI is in play.

Override in the same message: `0 reviews`, `1 review`, `3 reviews`, `Opus exhausted. Use Grok for review.`, `no browser QA`. More than 3 reviews needs an explicit ask. Extra reviews do not add repairs. `up to 2` or `stop early` turns the count into a ceiling.

Pinned ids (for traces, not for prompts):

| Agent | Pin |
|---|---|
| `cur-support`, `cur-browser` | `grok-4.7[effort=high,fast=false]` |
| `cur-reviewer` | `claude-opus-5-5[effort=high]` |
| `cur-reviewer-grok` | `grok-4.7[effort=high,fast=false]` · only after you say Opus is unavailable |

A prompt cannot retarget a pinned agent. If a trace is Fast, Medium, or Composer, that pass missed the route. `inherit` is not a pin.

**Opus limit:** stop. Say `Opus exhausted. Use Grok for review.` That launches `cur-reviewer-grok` and labels the result **same-family**. Do not ask `cur-reviewer` to switch.

**Codex:** `$dev-workflow` and friends stay in `~/.agents/skills` for Codex. In Cursor they are explicit-only. If a chat starts using `$dev-verify` or Sol/Luna, stop — that is the wrong family.

## Which command

| Situation | Command |
|---|---|
| Approved plan or a real change | `/cur-dev` |
| Tests, lint, types, docs | `/cur-verify` |
| Review only | `/cur-review` |
| Failing CI | `/cur-ci` |
| Running UI / Test Lab | `/cur-qa` |
| Commit, push, open a PR | `/cur-ship` |
| Eval smoke / rag-journey | `/cur-rag-eval` |
| Write-up a failure, no fix | `/cur-investigate` |

`/cur-dev` writes `artifacts/cursor-runs/<date>-<slug>.md` and, on `main`, opens a feature branch. Close with `/cur-ship`. Hooks ask before `git commit`, `git push`, and `gh pr merge`.

## Local app

Three terminals. Console `http://127.0.0.1:5173/operator/` · Lab `http://127.0.0.1:5173/operator/lab` · API `http://127.0.0.1:8000`.

```powershell
cd backend
.\venv\Scripts\Activate.ps1
python -m app
```

```powershell
cd backend
.\venv\Scripts\Activate.ps1
python worker.py
```

```powershell
cd frontend
pnpm dev
```

## Prompts

Compact first. Longer forms are for overrides or first-time reading.

### Approved plan

Picker: **Grok 4.7 XHigh**.

```text
/cur-dev Implement @docs/plans/search-filters.md. This plan is approved.
Focus review on authorization and compatibility. No browser QA.
Report any material plan conflict before expanding scope.
```

Override example (only the deltas):

```text
/cur-dev Implement @docs/plans/search-filters.md. This plan is approved.
1 review, 1 repair. No browser QA.
```

### Small UI fix

Picker: **Grok 4.7 High**.

```text
/cur-dev Fix the empty-state text overlapping the retry button on mobile.
Browser smoke at 375px on http://127.0.0.1:5173/operator/lab.
Skip docs unless the behavior needs explanation.
```

### Checks only

Picker: **Grok 4.7 High**.

```text
/cur-verify Check only backend/app/modules/retrieval and its tests.
Do not edit source, tests, snapshots, or config.
```

### Tests and docs

```text
/cur-verify Add regression coverage for the retry behavior and update its API docs.
Application behavior is out of scope. Report implementation bugs; do not fix them.
```

### Review only

```text
/cur-review Review this branch against origin/main, including uncommitted changes.
Focus on tenant isolation, error handling, and migrations.
```

PR form:

```text
/cur-review Review PR 25. Focus on retrieval latency and grounding.
```

Opus exhausted:

```text
/cur-review Opus exhausted. Use Grok for review.
Review this branch against origin/main. Same-family is acceptable.
```

### CI

Picker: **Grok 4.7 High**. No URL needed if `gh` can see the repo.

```text
/cur-ci Fix the latest failing run on this branch.
Do not push or rerun remote jobs.
```

### Browser QA

Fresh task. Picker: **Grok 4.7 High**. App already running. Attach the tab if you can.

```text
/cur-qa Inspect the existing Cursor tab at http://127.0.0.1:5173/operator/lab
Test Lab > Messages. Findings only. Confirm the project first.

Three authorized questions:
1. <answerable, known expected answer>
2. <absent from the knowledge base>
3. <follow-up that needs conversation context>
```

### One milestone

Picker: **Grok 4.7 XHigh**.

```text
/cur-dev Implement only milestone 1 in @docs/plans/storage-migration.md.
Review rollback safety. No production migration. No browser QA.
Stop after milestone 1 with a handoff for milestone 2.
```

### Two-pass review (default count, named focus)

```text
/cur-dev Implement @docs/plans/permissions-hardening.md.
First review: correctness and authorization.
Second review: authorization boundaries and repaired paths only.
```

### Mid-task policy

```text
Skip browser QA and docs for the rest of this task.
One extra focused review of the authorization changes.
Do not increase the repair count. Report remaining counters first.
```

### Close

Picker: **Grok 4.7 High**.

```text
/cur-ship Open a PR for this branch. Watch checks. Do not merge.
```

### Eval / investigate

```text
/cur-rag-eval Run make eval-smoke and compare the tax_v1 fixture, repeat 3.
```

```text
/cur-investigate Why Test Lab messages stream but citations stay empty.
Write one note under docs/operations/. Do not fix code.
```

## What you get back

Changes, commands and results, review/QA findings with coverage, skipped items, remaining risk, ledger path. Local `make quality` is not a green remote run.
