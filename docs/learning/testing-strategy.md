# Testing Strategy

This document explains how APE tests are organized, how fixtures work, and how
to run unit vs integration tests — including tests that work without external
services.

---

## Goals

| Goal | How |
| ---- | --- |
| Fast feedback | Unit tests with no network or DB |
| Realistic HTTP behavior | Integration tests through ASGI transport |
| CI-ready | No mandatory Docker for test suite |
| Type safety | Mypy on `app/` package |
| Consistent style | Ruff lint + format in pre-commit |

---

## Layout

```text
tests/
├── conftest.py              # Shared fixtures, test env setup
├── unit/
│   ├── test_config.py       # Settings, DSNs, CORS parsing
│   ├── test_logging.py      # structlog configuration
│   └── test_schemas.py      # ApiResponse / ErrorResponse
└── integration/
    └── test_health_endpoints.py   # Full ASGI stack
```

Configuration lives in `pyproject.toml` under `[tool.pytest.ini_options]`:

- `pythonpath = ["backend"]` — import `app` without installing the package.
- `asyncio_mode = "auto"` — async tests run without explicit markers.
- Markers: `unit`, `integration`.

---

## Test environment isolation

`tests/conftest.py` sets environment **before** importing the application:

```python
os.environ.setdefault("APE_APP__ENV", "testing")
os.environ.setdefault("APE_LOGGING__RENDER_JSON", "false")
```

This prevents a developer's local `.env` from affecting test outcomes.

`get_settings.cache_clear()` in the `settings` fixture ensures a fresh parse.

---

## The `client` fixture (integration)

```python
@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            yield ac
```

| Component | Role |
| --------- | ---- |
| `create_app()` | Full application wiring |
| `LifespanManager` | Runs startup/shutdown (creates DB clients) |
| `ASGITransport` | Sends requests through FastAPI without a live server |
| `httpx.AsyncClient` | Async HTTP client for assertions |

Tests exercise middleware, DI, handlers, and exception paths end-to-end.

---

## Running without external services

Integration tests **do not require** PostgreSQL, Redis, Qdrant, or MinIO:

- Lifespan creates clients but probes log warnings instead of crashing.
- `/ready` returns 200 or 503 depending on reachability — both are valid assertions.
- `/health` always returns 200 while the process runs.

This lets `pytest` pass in CI before Docker services start.

To test against a live stack, run `docker compose up -d` first; `/ready` should
report all dependencies as `ok`.

---

## Unit test examples

### Configuration (`test_config.py`)

- DSN generation (async + sync).
- Password URL encoding for special characters.
- CORS comma-separated string parsing.
- Environment flag properties (`is_production`, etc.).

### Schemas (`test_schemas.py`)

- `ApiResponse.ok()` shape.
- `ErrorResponse` serialization excludes `None` fields.

### Logging (`test_logging.py`)

- Root logger level matches settings.
- `configure_logging()` is idempotent.
- Log emission does not raise.

---

## Integration test examples

### Health endpoints (`test_health_endpoints.py`)

| Test | Asserts |
| ---- | ------- |
| `test_health_returns_ok` | 200, `success: true`, `environment: testing` |
| `test_health_sets_correlation_headers` | `X-Request-ID`, `X-Trace-ID` present |
| `test_health_honors_inbound_request_id` | Inbound ID echoed back |
| `test_ready_reports_dependency_breakdown` | All four deps listed |
| `test_unknown_route_returns_standard_error` | 404 `ErrorResponse` envelope |

---

## Tooling commands

```bash
pytest                          # full suite
pytest -m unit                  # unit only
pytest -m integration           # integration only
pytest --cov --cov-report=term-missing   # coverage
ruff check . && ruff format --check .      # lint + format
mypy                            # type check
make check                      # lint + mypy + test
pre-commit run --all-files      # all git hooks
```

---

## Writing tests for new features

When adding a module (e.g. Projects):

1. **Unit tests** — service logic with faked repositories/providers.
2. **Integration tests** — HTTP routes via `client` fixture.
3. **Contract tests** (later) — provider implementations against abstract interfaces.

Pattern for service unit tests (future):

```python
async def test_create_project_calls_repository():
    repo = AsyncMock(spec=ProjectRepository)
    repo.add.return_value = project_entity
    service = ProjectService(repo=repo, session=mock_session)
    result = await service.create(...)
    repo.add.assert_called_once()
```

---

## Common mistakes

| Mistake | Fix |
| ------- | --- |
| Importing `app` before setting test env | Keep env setup at top of `conftest.py` |
| Starting uvicorn in tests | Use `ASGITransport` |
| Asserting `/ready` is always 200 | Accept 200 or 503 without infra |
| Skipping `LifespanManager` | Lifespan wiring won't run |

---

## Key files

| File | Role |
| ---- | ---- |
| `tests/conftest.py` | Env isolation, `client` fixture |
| `pyproject.toml` | Pytest, coverage, mypy config |
| `.pre-commit-config.yaml` | Ruff hooks on commit |
| `Makefile` | `make test`, `make check` |
