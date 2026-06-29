# Module Template

## Folder structure

```text
modules/<feature>/
    __init__.py
    services/
        <feature>_service.py
    repositories/
        <feature>_repository.py   # extends ProjectScopedRepository
    schemas/
        <feature>.py
    models/
        <feature>.py              # compose UUIDPrimaryKeyMixin, TimestampMixin, ProjectScopedMixin
```

HTTP routes belong in the composition layer:

```text
api/v1/routes/<feature>.py        # FastAPI router + Depends() wiring
```

## Checklist

- [ ] Models compose `ProjectScopedMixin` for Project-owned entities
- [ ] Repositories extend `ProjectScopedRepository` (not unscoped base)
- [ ] Service owns `commit()` / `rollback()`
- [ ] Routes in `api/v1/routes/` use `ApiResponse` from `platform.http.envelopes`
- [ ] No imports from `app.dependencies` inside `modules/`
- [ ] No vendor SDK imports outside `platform/providers/implementations/`
- [ ] Long-running work via `JobQueue.enqueue()` (when jobs ship), not HTTP
- [ ] Models registered in `app/composition/orm_registry.py`
- [ ] Router registered on `api/v1/router.py`
- [ ] Feature doc in `docs/features/<feature>.md`
- [ ] Unit + integration tests
