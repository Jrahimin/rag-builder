# Index Lifecycle API

**Prefix:** `/api/v1/projects/{project_id}/index-builds`

Every endpoint is authenticated and Project-scoped. Long-running actions return
`202` with a durable `job_id`; inspect progress and structured results through
the [Jobs API](./jobs_api.md).

## GET ``

Lists recent immutable builds and the Project's active/previous pointer state.
Only `validated` or `retained` builds may be activated.
This read-only listing does not resolve the Project's current generation model;
a stale model selection cannot prevent inspection of its existing build state.

## POST `/reembed`

Stages a full vector and keyword snapshot. The completed build remains
`validated` until explicitly activated. Activation marks included documents
`ready`. This is the operator Rebuild index action.

## POST `/reindex`

Compatibility alias for `/reembed`. Both jobs run the same full snapshot
workflow; they differ only in the stored operation label.

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

Private structural reprocessing: POST `/api/v1/projects/{project_id}/index-builds/reprocess-private`; see [private structural builds](../features/private_structural_builds.md). Migration0035 isolates chunk generations; it never automatically activates.

Private structural builds use persisted `structure.v1` intent and durable `corpus.structure.v1`. POST `index-builds/{build_id}/revalidate-private` copies exact final rows and compatible vectors into a new private build; preview/replay/activation verify the structural manifest and vector input identity. Apply migration0036 before use. See [private structural acceptance](../features/private_structural_builds.md).
