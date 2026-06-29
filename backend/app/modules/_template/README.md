# Module Template

## Folder structure

```text
app/models/                       # All ORM entities (shared across modules)
    project.py
    ...

modules/<feature>/
    __init__.py
    services/
        <feature>_service.py
    repositories/
        <feature>_repository.py   # extends AsyncRepository or ProjectScopedRepository
    schemas/
        <feature>.py              # module-specific Pydantic request/response models
```

HTTP routes belong in the composition layer:

```text
api/v1/routes/<feature>.py        # FastAPI router + Depends() wiring
```

## Repository patterns

Pick **mixins per entity** — not every table needs soft delete or `is_active`.

| Entity type | Repository base | ORM location | Typical mixins |
| ----------- | ----------------- | ------------ | -------------- |
| **Aggregate root** (Project) | `AsyncRepository` | `app/models/` | UUID, Timestamp, Active, SoftDelete |
| **Project-owned** | `ProjectScopedRepository` | `app/models/` | UUID, Timestamp, ProjectScoped + lifecycle as needed |

Register every new model in `app/composition/orm_registry.py`.

Shared list filters: `LifecycleListFilters` / `ListParams` (`include_deleted`, `is_active`).
Shared lifecycle helpers: `mark_soft_deleted`, `is_soft_deleted`.
Shared service helpers (`platform/domain/`): `get_or_raise`, `list_paginated`, `soft_delete`, `update_active_status`, `flush_commit_refresh`. Keep `create` / `update` in the concrete service.

## Checklist

- [ ] ORM models live in `app/models/`, not inside `modules/`
- [ ] Project-owned repositories extend `ProjectScopedRepository`
- [ ] Aggregate roots extend `AsyncRepository` (module-local subclass if needed)
- [ ] Service owns `commit()` / `rollback()`
- [ ] Routes in `api/v1/routes/` use `ApiResponse` from `platform.http`
- [ ] No imports from `app.dependencies` inside `modules/`
- [ ] No vendor SDK imports outside `platform/providers/implementations/`
- [ ] Long-running work via `JobQueue.enqueue()` (when jobs ship), not HTTP
- [ ] Models registered in `app/composition/orm_registry.py`
- [ ] Router registered on `api/v1/router.py`
- [ ] Feature doc in `docs/features/<feature>.md`
- [ ] API reference in `docs/api/<feature>.md` (update `docs/api/README.md` index)
- [ ] Unit + integration tests
