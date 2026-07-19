# Contributing

RAG Builder is a Python 3.12/FastAPI modular monolith with a React/TypeScript
operator console. Contributions should stay focused, project-scoped, and
provider-agnostic.

## Setup

Copy `.env.docker.example` to `.env.docker`, then start the development stack:

```bash
docker compose --env-file .env.docker up -d --build
```

For host-side backend tools, create a Python 3.12 virtual environment and run:

```bash
python -m pip install -r backend/requirements/dev.txt
```

For the console:

```bash
cd frontend
pnpm install --frozen-lockfile
```

## Quality and tests

Run the complete deterministic gate before opening a pull request:

```bash
make quality
```

Focused targets include `format-check`, `lint`, `typecheck`, `test-unit`,
`test-integration`, `migration-check`, `eval-smoke`, `frontend-quality`, and
`frontend-build`. Integration tests apply migrations only to the explicitly
configured disposable test database.

## Migrations

All schema changes require Alembic. Start PostgreSQL and the backend, then create
and review a revision:

```bash
make up-db
make up-backend
make migration-new name="describe the schema change"
make migrate
make migration-check
```

Never auto-create production tables at application startup. Include migration
upgrade coverage and, when a downgrade is safe, explain its limitations.

## Architecture boundaries

- `Project` is the mandatory data-isolation boundary.
- Routers validate and serialize; services own business orchestration and
  transactions; repositories own relational persistence; providers own vendor
  SDKs and external infrastructure.
- ORM models live in `backend/app/models/`; HTTP routes live in
  `backend/app/api/`; feature logic lives in `backend/app/modules/`.
- Keep long-running parsing, OCR, embedding, indexing, and evaluation work in
  durable background jobs.
- Do not introduce unscoped queries, direct vendor calls from services, or a new
  infrastructure category without an approved architectural decision.

The import-boundary tests are part of `make quality`.

## Pull requests

Keep pull requests small and explain the user or operator outcome, risks, test
evidence, configuration changes, migrations, and documentation impact. Add
focused tests for behavior changes, update generated OpenAPI types when the API
contract changes, and avoid unrelated formatting or refactors. CI must pass
without paid-provider credentials or public network dependencies.
