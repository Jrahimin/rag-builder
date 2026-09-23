# RAG Builder

Verified local facts for this checkout. Product scope and engineering rules stay in `.cursor/rules/project-context.mdc` and `.cursor/rules/architecture.mdc`. Architecture and feature references live under `docs/architecture/` and `docs/features/`. Skill prompts: `docs/learning/cursor-workflows.md`. Project extras: `.agents/skills/cur-rag-eval`, `.agents/skills/cur-investigate`. Run ledgers: `artifacts/cursor-runs/`.

## Runtime

- Python `>=3.12`. Backend virtualenv: `backend/venv`. Dependencies: `python -m pip install -r requirements/dev.txt` from `backend`.
- Frontend: pnpm `9.15.4`. CI uses Node 22.
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

`python -m app` reads `backend/.env` and serves `APE_SERVER__PORT` (default 8000). `python worker.py` starts `taskiq worker app.worker.entrypoint:broker`.

## Checks

The repository gate is `make quality`. It runs format, lint, typecheck, migration check, unit tests, integration tests, evaluation smoke, and frontend quality. It does not run the Docker image build.

Focused commands: `make format-check`, `make lint`, `make typecheck`, `make test-unit`, `make test-integration`, `make migration-check`, `make migration-drift-check`, `make eval-smoke`, `make frontend-quality`, `make frontend-build`.

Local migrations: `make migrate-local` (`cd backend && python -m alembic upgrade head`).

## CI

Workflow: `.github/workflows/ci.yml`, on pull requests and pushes to `main`.

- `quality`: Ubuntu, pgvector/pgvector `0.8.1-pg16`, Redis 7, Python 3.12, pnpm 9.15.4, Node 22. Applies `make migrate-local`, `make migration-drift-check`, and `make quality`. Sets auth off, hash embeddings, and the echo LLM.
- `docker-builds`: `docker compose config` and `docker compose build backend frontend`, plus loopback port checks.

A green local `make quality` does not mean the remote workflow or the Docker job passed.

## UI

Host processes above:

- Operator console: `http://127.0.0.1:5173/operator/`
- Test Lab: `http://127.0.0.1:5173/operator/lab`
- API: `http://127.0.0.1:8000` (`/health/live`, `/health/ready`)
- Vite base `/operator/`. `/api` and `/health` proxy to `http://localhost:8000`.

Compose `make up` publishes the console on `http://127.0.0.1:3010/operator/` and the API on `http://127.0.0.1:8010`.

`AuthConfig.enabled` defaults to false. This repo has no QA password. Use the existing browser session and confirm the project before interacting. Hosted authentication is described in `.cursor/rules/project-context.mdc`.

## Constraints

- Generated OpenAPI types: `frontend` scripts `api:schema`, `api:types`, and `api:generate`.
- Alembic revisions live under `backend/app/composition/migrations/versions`. Check drift with `make migration-drift-check`.
- When behavior, contracts, usage, or operations change, update the matching doc under `docs/features/` and the API reference under `docs/api/`.
