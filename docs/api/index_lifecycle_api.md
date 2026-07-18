# Index Lifecycle API

**Prefix:** `/api/v1/projects/{project_id}/index-builds`

Every endpoint is authenticated and Project-scoped. Long-running actions return
`202` with a durable `job_id`; inspect progress and structured results through
the [Jobs API](./jobs_api.md).

## GET ``

Lists recent immutable builds and the Project's active/previous pointer state.
Only `validated` or `retained` builds may be activated.

## POST `/reembed`

Stages a full vector and keyword snapshot with operation `reembed`. The completed
build remains `validated` until explicitly activated.

## POST `/reindex`

Stages a full vector and keyword snapshot with operation `reindex`. The completed
build remains `validated` until explicitly activated.

## POST `/{build_id}/activate`

Atomically activates a complete validated/retained build and retains the former
active build as the rollback target. Returns `400` when the build is partial,
failed, active, superseded, or otherwise ineligible.

## POST `/rollback`

Atomically restores the retained previous build. Returns `400` when no verified
rollback target exists.

## POST `/reconcile-storage`

Stages a read-only comparison of Project document storage keys with the object
store. The completed Job `result` includes `expected`, `actual`, `missing`,
`orphan`, and `consistent`.

## Related document actions

- `POST /documents/{document_id}/reprocess` — durable parse/chunk/full-build flow.
- `DELETE /documents/{document_id}` — durable reversible delete; returns `202`.
- `DELETE /documents/{document_id}/purge` — durable irreversible purge; returns
  `202`.
