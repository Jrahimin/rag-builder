# RAG Builder

Verified local facts for this checkout. Product scope and engineering rules stay in `.cursor/rules/project-context.mdc` and `.cursor/rules/architecture.mdc`. Architecture and feature references live under `docs/architecture/` and `docs/features/`.

Cursor prompts: `docs/learning/cursor-workflows.md` (`/cur-*`); preserve the existing project extras `.agents/skills/cur-rag-eval` and `.agents/skills/cur-investigate`. Codex recipes: `docs/learning/codex-workflows.md` (`$skill-name`); the explicit project skill is `.agents/skills/rag-evaluate`. Codex discovers skills in project/ancestor and user `.agents/skills`; use user workflow skills without copying them into this repository. Cursor ledgers stay under `artifacts/cursor-runs/`; Codex uses ignored `artifacts/codex-runs/`.

## Runtime

- Python `>=3.12`. Backend virtualenv: `backend/venv`. Dependencies: `python -m pip install -r requirements/dev.txt` from `backend`.
- Frontend: pnpm `11.22.0`. CI uses Node 24.
- Local app: three host processes, each in its own terminal. Diagnostic from `backend` with the venv active: `python -m app.cli doctor`.

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
pnpm install --frozen-lockfile
pnpm dev
```

`python -m app` reads `backend/.env` and serves `APE_SERVER__PORT` (default 8000). This checkout's documented local example is 8088; check the effective value before diagnosing a connection failure. `python worker.py` starts `taskiq worker app.worker.entrypoint:broker`.

## Checks

The repository gate is `make quality`. It runs format, lint, typecheck, migration check, unit tests, integration tests, evaluation smoke, and frontend quality. It does not run the Docker image build.

When Make is unavailable on Windows, use `backend\venv\Scripts\python.exe scripts\check_windows.py --target quality --dry-run` first, then the applicable target. See `docs/learning/codex-workflows.md` for explicit disposable-test configuration and `--allow-integration`. The adapter verifies Make recipes/prerequisites and refuses drift, downloads or automatic migration application. Integration fixtures can migrate the explicitly allowed disposable test DB.

Once per clone: `python -m pre_commit install` so `git commit` runs Ruff format/lint from `.pre-commit-config.yaml`. That is the cheap gate CI also starts with (`make format-check`).

Focused commands: `make format-check`, `make lint`, `make typecheck`, `make test-unit`, `make test-integration`, `make migration-check`, `make migration-drift-check`, `make eval-smoke`, `make frontend-quality`, `make frontend-build`.

Local migrations: `make migrate-local` (`cd backend && python -m alembic upgrade head`).

## CI

Workflow: `.github/workflows/ci.yml`, on pull requests and pushes to `main`. Repair overrides: `.github/ci-repair.md`.

- `quality`: Ubuntu, pgvector/pgvector `0.8.1-pg16`, Redis 7, Python 3.12, pnpm 11.22.0, Node 24. Applies `make migrate-local`, `make migration-drift-check`, and `make quality`. Sets auth off, hash embeddings, and the echo LLM.
- `docker-builds`: `docker compose config` and `docker compose build backend frontend`, plus loopback port checks.

A green local `make quality` does not mean the remote workflow or the Docker job passed.

## UI

Host processes above:

- Operator console: `http://127.0.0.1:5173/operator/`
- Test Lab: `http://127.0.0.1:5173/operator/lab`
- API: `http://127.0.0.1:<APE_SERVER__PORT>` (`/health/live`, `/health/ready`); the default port is 8000.
- Vite base `/operator/`. `/api` and `/health` proxy to `VITE_API_PROXY_TARGET`, defaulting to `http://localhost:8000` in `frontend/vite.config.ts`. Set the target to the effective backend address before starting `pnpm dev` when the ports differ, for example `$env:VITE_API_PROXY_TARGET = "http://localhost:8088"` in that frontend PowerShell session.

For local UI or API QA, check both the backend's direct `/health/live` and Vite's proxied `/health/live`, then confirm the browser is signed in and the intended project is selected. A successful direct check alone does not prove Vite is targeting that backend.

For Codex browser QA, bind the exact owning authenticated tab/project and discover supported DevTools/CDP capabilities before capture. Enable observation before the authorized action and collect actual response bodies, request/message IDs and timings. Fetch-based SSE needs parsed stream evidence; UI results or GET alone do not prove SSE parity. Respect site/tool permissions and report unavailable body/stream capture as a gap; separately authorized API replay is different evidence.

If Git reports dubious ownership under the Windows sandbox account, use a one-command exception such as `git -c safe.directory=E:/python-projects/rag-builder status --short` after confirming the workspace path. Do not add a global `safe.directory` entry for this diagnostic.

Compose `make up` publishes the console on `http://127.0.0.1:3010/operator/` and the API on `http://127.0.0.1:8010`.

`AuthConfig.enabled` defaults to false; local authentication may be enabled in `backend/.env`. This repo has no QA password. Use the existing browser session and confirm the project before interacting. Hosted authentication is described in `.cursor/rules/project-context.mdc`.

## Constraints

- Generated OpenAPI types: `frontend` scripts `api:schema`, `api:types`, and `api:generate`.
- Alembic revisions live under `backend/app/composition/migrations/versions`. Check drift with `make migration-drift-check`.
- When behavior, contracts, usage, or operations change, update the matching doc under `docs/features/` and the API reference under `docs/api/`.

## Codex evidence and resume

Use the approved plan and ignored `artifacts/codex-runs/<run-id>/` checkpoint when resuming. Refresh source/dependency/runtime, immutable project configuration and corpus/build identity; retain used counters and invalidate stale evidence. Historical reports and isolated passing tests do not replace current required gates. Investigation/report-only requests remain read-only.

For RAG diagnosis follow extraction/corpus identity, scope/authority, retrieval/rerank, drafting/verification/recovery, terminal result/persistence, transport parity and UI. Use `$rag-evaluate` for saved captures/offline evaluation; live prompts, source/configuration changes and paid runs require corresponding authorization. Reuse `docs/features/provider_cost_controls.md` finite paid-evaluation controls; Cohere limits do not cap other paid providers. Never lower grounding or activate a candidate to claim success.
