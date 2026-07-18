# Safe Corpus and Index Lifecycle

Phase 5 makes corpus changes recoverable without exposing a partially written
retrieval index. Reprocess, re-embed, reindex, reversible delete, irreversible
purge, and storage reconciliation all use the existing durable job runtime.

## Architecture

`IndexBuild` is a Project-scoped, write-once retrieval snapshot. While a build is
private in `building`, its worker writes a complete set of vector rows, keyword
rows, term statistics, and a document/version manifest under its `index_build_id`.
Validation seals the snapshot as `validated`; failed or partial builds are never
searchable.

`ProjectIndexPointer` contains only `active_build_id` and
`previous_build_id`. Activation locks this row, verifies that the target is a
complete validated/retained build, and swaps both pointers in one database
transaction. The old active build becomes `retained`. Rollback performs the same
operation with the retained previous build.

```text
durable lifecycle job
        |
        v
private full build --validate--> validated
        | failure                 |
        v                         v
      failed              atomic pointer swap
                                  |
                                  v
                         retrieval reads active only
```

## Document lifecycle

- Reprocess increments `document.version`, regenerates parse/chunk artifacts,
  then creates and activates a full corpus build.
- Re-embed and reindex create new full builds beside the active build. The
  manual Project actions stop at `validated`; an operator explicitly activates
  them.
- Delete is reversible. Its job builds and activates a corpus excluding the
  document, then sets `deleted_at`. The retained prior build and document
  artifacts make rollback possible.
- Purge is irreversible. It first activates an excluding build, then removes the
  document's chunks, vectors, keyword rows, raw object, parsed sidecars, and
  relational record. Retained builds containing the document are superseded and
  cannot be activated.

All actions use stable configuration/version-aware idempotency keys. Job progress,
result data, structured failures, and audit events remain available through the
Jobs and operator APIs.

## Upload safety

Uploads are size-bounded and spooled once. Before object storage or background
processing, the API verifies the supported extension, declared MIME type,
content signature, basic structural integrity, UTF-8 text validity, PDF
encryption/EOF markers, and DOCX ZIP structure. Stable error codes distinguish
unsupported type, MIME mismatch, signature mismatch, corrupt input, and
password-protected PDF.

`BaseMalwareScanner` is the narrow provider boundary. Development/test defaults
to the explicit disabled provider. Production startup requires `clamav`, whose
provider uses ClamAV's TCP `INSTREAM` protocol. Scanner unavailability fails the
upload closed with `malware_scanner_unavailable`; detection fails with
`document_malware_detected`. Neither path stores the upload or creates a job.

Configuration:

```dotenv
APE_MALWARE_SCAN__BACKEND=clamav
APE_MALWARE_SCAN__HOST=clamav
APE_MALWARE_SCAN__PORT=3310
APE_MALWARE_SCAN__TIMEOUT_SECONDS=15
```

## Storage reconciliation

`storage.reconcile` lists the Project storage prefix and compares it with every
raw and parsed key implied by current document records. The durable job result
reports expected, actual, missing, orphan, and `consistent`; it does not delete
objects automatically. Repair remains an explicit operator decision so a
transient database or storage problem cannot turn reconciliation into data loss.

## Production considerations

- Keep at least the active and previous builds. Superseded builds can be cleaned
  only by a future retention policy.
- Monitor failed/building builds, lifecycle job age, ClamAV health, and
  reconciliation drift.
- Activation is metadata-only and atomic; full build cost occurs before it.
- This slice deliberately does not add connectors, legal hold, multi-region
  coordination, or a general-purpose index platform.

## Verification

Unit and integration coverage exercises file validation, scanner failure,
idempotent jobs, partial/failed activation rejection, pointer activation and
rollback, active-search isolation, delete/purge cleanup, storage reconciliation,
and migration/model parity.
