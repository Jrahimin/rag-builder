# Codex recipes for RAG Builder

Use the [canonical development guide](C:/Users/user/.agents/skills/dev-workflow/references/Codex-Development-Workflow-Guide.md) for generic skill selection, routing, cumulative budgets, resume and permission rules. This page adds repository-specific commands and proof requirements. Cursor `/cur-*` recipes stay in [cursor-workflows.md](cursor-workflows.md); Codex skills use `$skill-name`.

| Need | Starting skill | Evidence to finish |
| --- | --- | --- |
| Diagnose a timeout or wrong answer | `$dev-investigate` | Failing execution stage, actual saved body/trace and precise uncertainty |
| Implement your approved bounded plan | `$dev-workflow` | Current deterministic checks, independent review and required journey acceptance |
| Compare captures or evaluate RAG | `$rag-evaluate` | Manifest/config/source identity, outcomes, grounding and required transport parity |
| Inspect Test Lab and actual API bodies | `$browser-qa` | Exact signed-in tab/project, correlated JSON/SSE bodies and visible result |
| Reproduce failed CI | `$ci-fix` | Exact failing job/revision and local evidence distinguished from remote CI |

## Diagnose before rerunning

```text
$dev-investigate Diagnose the timeout using saved captures in [path],
current execution configuration and relevant code. Report the failing
stage, evidence and gaps. Do not edit code or send new messages.
```

Trace extraction and corpus/index identity → scope/authority → retrieval/rerank → draft/verification/recovery → terminal result/persistence → Regular/SSE/GET → UI. Read the latest project records, then refresh applicable state. Historical gates, restored login or old immutable revision numbers are not current proof. Keep applicable period separate from publication recency and captured answers separate from verified legal expectations.

## Windows repository gates

Run from repository root; the adapter reads Makefile before executing anything:

```powershell
.\backend\venv\Scripts\python.exe scripts\check_windows.py --target quality --dry-run
.\backend\venv\Scripts\python.exe scripts\check_windows.py --target test-unit
.\backend\venv\Scripts\python.exe scripts\check_windows.py --target eval-smoke
.\backend\venv\Scripts\python.exe scripts\check_windows.py --target frontend-quality
.\backend\venv\Scripts\python.exe scripts\check_windows.py --target frontend-build
```

Supported targets: `format-check`, `lint`, `typecheck`, `migration-check`, `migration-drift-check`, `test-unit`, `test-integration`, `eval-smoke`, `frontend-quality`, `frontend-build`, `quality` (default).

The adapter uses installed backend venv, Python >=3.12 (CI 3.12), Node 24 and pnpm 11.22.0. It blocks on recipe/tool drift and disables Corepack downloads. Install/update dependencies separately through the project's authorized setup; frontend installs use the existing frozen lockfile. pnpm scripts use `verifyDepsBeforeRun: error` so stale dependency state requires explicit frozen setup instead of an automatic install during checks. No automatic installation, Docker startup or `alembic upgrade` occurs. The integration fixtures themselves can migrate the explicitly allowed disposable DB.

For `quality`, `test-integration` or `migration-drift-check`, verify a disposable loopback DB and Redis first. Set explicit session values for `APE_APP__ENV=testing`, `APE_DATABASE__HOST`, `APE_REDIS__HOST`, matched `APE_DATABASE__NAME`/`APE_TEST_DATABASE__NAME` ending `_test`; integration also needs `APE_TEST_DATABASE__ALLOW_MIGRATIONS=true`. Credentials remain private. `LOCAL` or a reachable DB alone is insufficient. Then, only with authorization covering those fixtures:

```powershell
.\backend\venv\Scripts\python.exe scripts\check_windows.py --target quality --allow-integration
.\backend\venv\Scripts\python.exe scripts\check_windows.py --target migration-drift-check --allow-integration
```

`quality` exactly expands Make's current quality prerequisites and stops on the first failure. Migration drift is separate from static `migration-check`; schema preparation is separately authorized. A local pass does not establish remote CI or Docker acceptance. Record different tool versions and distinguish isolated tests from combined-suite results.

## Saved captures and offline evaluation

```text
$rag-evaluate Inspect [capture manifest] against the approved acceptance
criteria and verified source labels. Preserve frozen captures; report
transport, execution, grounding, completeness and parity separately.
Do not send messages, change project revisions or activate candidates.
```

Local capture normalization (from `backend`; choose a new output filename):

```powershell
.\venv\Scripts\python.exe -m app.cli.acceptance_report --input [capture.json] --output [new-normalized.json]
```

The module validates a bounded local package without DB/network action. It is not an `app.cli` dispatcher subcommand. Passing schema normalization establishes package shape only; semantic labels and actual response parity still need evidence.

## Authorized live acceptance

Read [provider expense controls](../features/provider_cost_controls.md) and [provider-cost API](../api/provider_costs.md). Preview eligible Cohere work before requesting a spend allowance:

```powershell
# From backend; read-only DB query, no provider dispatch:
.\venv\Scripts\python.exe -m app.cli provider-costs --project [uuid] --month [YYYY-MM] --preview-build
# Only for the explicitly approved isolated fixture and finite allowance:
.\venv\Scripts\python.exe -m app.cli rag-journey --fixture [fixture.json] --paid-eval --budget-usd [approved-positive-limit]
```

Preview requires approved access to the intended DB. Cohere limits cover scoped Cohere attempts in that deployment; they do not automatically cap LLM/translation providers or other deployments. Each journey invocation currently generates a fresh run token. Reserve its maximum provider cost and permitted request/write counts in the parent task ledger before dispatch; separate commands, repeats and comparisons consume the same approved cumulative allowance. Unknown dispatch outcomes keep their reservation, and actual supported cost is reconciled afterward. This workflow ledger does not replace provider enforcement, intercept arbitrary calls or cap unaccounted providers; if a provider cannot be bounded, stop before dispatch. Do not activate accounting/cache/deployment flags as a side effect of QA.

Before live messages, confirm code fingerprint, effective project execution revision, source generation/index build, active/candidate identity and manifest. Start with an authorized representative diagnostic subset when needed, then satisfy the full approved matrix. Capture request/message IDs, status, timings, actual JSON bodies and hashes; preserve source labels and frozen prompts. Never count a timeout as successful abstention or use 200/citations as answer proof.

```text
$browser-qa Use the attached signed-in Test Lab tab for [project].
Capture network bodies for the already authorized scenarios in [manifest],
correlate Regular/SSE/GET with the UI and report gaps. Do not create
replacement sessions, send extra prompts or change configuration.
```

Use supported browser DevTools/CDP capability discovery and the maintained generic network-capture helper/reference. It accepts the documented capability from the owning tab, exact URL/method selectors, finite caps and an application-specific terminal predicate. Capture once before the approved UI action; JSON uses completed response bodies, fetch SSE uses native buffered/incremental bytes. A terminal frame with loadingFailed remains a transport gap. Observe before acting; distinguish UI capture from separately approved API replay. Fetch-based SSE requires actual parsed stream evidence. Missing bodies, denied access and unavailable required parity remain explicit gaps.

## Resume and handoff

Codex drafts the contract from actual chat/plan approval and discovers matching runs; you do not need to create ledger files manually. Independent review is a mandatory contract criterion. Keep ignored ledgers under `artifacts/codex-runs/<run-id>/`. Resume the same approved plan with retained repair/review/tool counters; refresh changed repository, dependency, corpus, configuration and runtime inputs before using earlier passes. Do not repeat a consequential action with unknown outcome.

```text
$dev-workflow Resume the approved work in this project; discover the matching run (or use [run ID]). Refresh
stale evidence, preserve used budgets and continue unfinished authorized
work through verification, independent review and focused repair closure.
```

Report deterministic checks, runtime health, raw API, browser behavior, semantic/source assessment and paid-provider validation separately. Finish only when mandatory criteria have current evidence; otherwise hand off verified results plus failed, blocked or unverified gaps.

## Maintained adapter checks

Offline standard-library tests import the installed adjacent Windows adapter. They use disposable files and mocks, without DB, Redis, providers or dependency installation:

```powershell
.\backend\venv\Scripts\python.exe -m unittest discover -s scripts\tests -p test_check_windows.py
```

Keep Makefile and existing CI as current recipe authorities. The adapter fails closed on drift. A shared cross-platform recipe runner is a separate future project refactor; this skill refinement does not change application/CI commands.
