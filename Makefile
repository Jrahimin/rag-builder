PYTHON ?= python
PNPM ?= pnpm
COMPOSE = docker compose --env-file .env.docker

.PHONY: help up down restart status logs rebuild up-backend up-frontend up-worker up-db up-redis up-storage \
	migrate migrate-local migration-new migration-check migration-drift-check doctor health format format-check \
	lint typecheck test-unit test-integration eval-smoke frontend-install frontend-format-check frontend-lint \
	frontend-typecheck frontend-test frontend-build frontend-quality quality

help:
	@echo "Run 'make quality' for the deterministic repository quality gate."
	@echo "See README.md for development and dedicated deployment commands."

up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

restart:
	$(COMPOSE) restart

status:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs -f --tail=200

rebuild:
	$(COMPOSE) build --no-cache backend worker frontend
	$(COMPOSE) up -d

up-backend:
	$(COMPOSE) up -d backend

up-frontend:
	$(COMPOSE) up -d --no-deps frontend

up-worker:
	$(COMPOSE) up -d worker

up-db:
	$(COMPOSE) up -d postgres

up-redis:
	$(COMPOSE) up -d redis

up-storage:
	$(COMPOSE) up -d minio minio-init

migrate:
	$(COMPOSE) run --rm migrate

migrate-local:
	cd backend && $(PYTHON) -m alembic upgrade head

migration-new:
	@test -n "$(name)" || (echo "Usage: make migration-new name='short description'" && exit 2)
	$(COMPOSE) exec backend alembic revision --autogenerate -m "$(name)"

migration-check:
	$(PYTHON) scripts/check_migrations.py

migration-drift-check:
	cd backend && $(PYTHON) -m alembic check

doctor:
	cd backend && $(PYTHON) -m app.cli doctor

health:
	curl --fail --silent http://localhost:8000/health/live
	curl --fail --silent http://localhost:8000/health/ready

format:
	$(PYTHON) -m ruff format backend tests scripts infra/hosted

format-check:
	$(PYTHON) -m ruff format --check backend tests scripts infra/hosted

lint:
	$(PYTHON) -m ruff check backend tests scripts infra/hosted

typecheck:
	$(PYTHON) -m mypy --no-incremental

test-unit:
	$(PYTHON) -m pytest tests/unit tests/architecture

test-integration:
	$(PYTHON) -m pytest tests/integration

eval-smoke:
	$(PYTHON) -m pytest tests/evaluation -m evaluation_smoke

frontend-install:
	cd frontend && $(PNPM) install --frozen-lockfile

frontend-format-check:
	cd frontend && $(PNPM) format:check

frontend-lint:
	cd frontend && $(PNPM) lint

frontend-typecheck:
	cd frontend && $(PNPM) typecheck

frontend-test:
	cd frontend && $(PNPM) test

frontend-build:
	cd frontend && $(PNPM) build

frontend-quality: frontend-format-check frontend-lint frontend-typecheck frontend-test

quality: format-check lint typecheck migration-check test-unit test-integration eval-smoke frontend-quality
