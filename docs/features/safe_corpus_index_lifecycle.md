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
- Parser 2.0.0 / chunker 3.0.0 OCR structure changes require that reprocess before
  a new build can contain typed table chunks. Historical conversation snapshots
  stay immutable; refresh or create a conversation after activation so chat uses
  the new evidence configuration.
- Rebuild index (`POST .../reembed`) creates a new full vector+keyword snapshot
  beside the active build. The Project action stops at `validated`; an operator
  explicitly activates it. Activation also marks included documents `ready`.
  `POST .../reindex` remains an equivalent compatibility alias.
- Query search uses the active build's embedding identity. A new rebuild may
  target a different provider from live settings; activate, then search uses
  that identity. Rollback restores the retained build without mixing vector
  spaces, and refuses if that historical provider key is gone. Unlabeled or mixed
  active builds fail closed with a configuration error instead of empty retrieval.
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
  Rollback and activation refuse if the target build's embedding credentials are
  gone or its identity is unlabeled/mixed, so the pointer cannot move into a
  search that would 409/503.
- This slice deliberately does not add connectors, legal hold, multi-region
  coordination, or a general-purpose index platform.

## Verification

Unit and integration coverage exercises file validation, scanner failure,
idempotent jobs, partial/failed activation rejection, pointer activation and
rollback, active-search isolation, delete/purge cleanup, storage reconciliation,
and migration/model parity.


## Quality receipt before activation

Integrity `validated` establishes counts, hashes and embedding membership. Pointer
moves additionally require an immutable `build.acceptance.v1` receipt stored in
`index_acceptances`. All explicit activation, rollback and automatic worker
paths use the same gate before pointer/readiness changes. New automatic builds
without a receipt remain validated with `validated_pending_quality_acceptance`;
destructive delete/purge work waits for activation and does not remove active
artifacts while quality acceptance is pending.

Receipts bind the current code fingerprint, full secret-free immutable runtime
configuration hash and revision, index configuration hash, source generation,
project/build, manifest/corpus and semantic row-metadata hashes, embedding identity,
acceptance-set revision and report/evidence hashes. Identity drift, failed cases,
incomplete proof or cross-project receipts reject activation. Receipts cannot be
updated; new evidence creates another receipt. Project cleanup may remove them.
Already-active legacy snapshots remain readable; new pointer moves require fresh
acceptance. No old immutable configuration is migrated to different semantics.

Authenticated project operators append/read receipts at the build acceptance API.
Production certificates bind an immutable project/build acceptance-set definition,
three repetitions per approved case and an active/candidate comparison report identity.
The captured Budget Speech remediation corpus retains `message-journey.v1`, Q1-Q7
plus earlier AGM, threshold, DNCC, rent and partnership cases. Other projects own
their approved case sets. Labels must come from source review.
`offline_fixture` receipts are restricted to hash embeddings and echo generation;
they certify disposable documentary fixtures and cannot certify paid/live builds.

Offline fixtures do not certify the actual corpus. Actual metadata reconciliation,
private reprocessing, real candidates/comparison, paid-provider benchmarks and
activation remain pending. Do not substitute embedding backends to clear this gate.

### Durable acceptance continuation and runtime identity

Delete/purge jobs persist `waiting_acceptance` with their sealed candidate and
unfinished destructive work. They do not report success. Once the candidate is
accepted and active, the durable dispatcher stages one continuation outbox intent;
restart and duplicate delivery retain lease fencing and completed-job idempotency.
Suspension does not consume a retry attempt. The normal worker completes removal
only after resumption. An unaccepted candidate never removes the original source.

Quality receipts bind the immutable executing process identity captured at package
initialization, including the execution contract. Deployments require process
restart; changing files cannot make an old process claim the new code identity.
Queued evaluation captures that identity and rejects a different executing runtime.
Approved acceptance sets are append-only per project/build, independently hashed;
receipts from another project, build or set do not satisfy activation.

Disposable journey tests may explicitly approve a genuine source-grounded fixture
receipt before continuing cleanup. Named `fixture-concept` vectors keep their
actual identity and are accepted only with testing + ape_test + configured
hash/echo. Ordinary tooling has no automatic receipt path and reports pending
quality obligations without an indefinite poll. Production and paid provider
identities remain subject to exact manifest/runtime matching.

### Observed production evidence

Production receipts must reference an immutable `acceptance.observed.v1` report,
its ID/hash and the report's active/candidate comparison hash. Publishing a report
validates captured public response bodies against actual persisted Project Messages,
preceding user turns, immutable configuration, executing runtime, source generation,
reviewed scope fingerprint and indexed citation hashes. A self-reported successful
case list alone cannot satisfy the production gate.

Labels bind the candidate inventory and exact reviewed source spans, or named missing
requirements with a review reason. The immutable approved acceptance definition owns
the required cases and repetitions on both the candidate and current distinct active
build. The remediation matrix is twelve cases times three repetitions times two builds
(72 Messages). The separate sixteen-question historical baseline remains optional;
extra labels or historical captures never add required repetitions, release cases or
release latency/quality metrics. Frozen question identities still apply to those labels.
Representative SSE and persisted GET captures must agree with the same persisted
proof, including every public citation locator, excerpt, evidence hash, supporting span,
authority field and assertion-evidence field. Schema defaults normalize missing
optional values; malformed or unknown nested proof fields are rejected. Completed turns retain attempted/rejected assertion counts and persistence
measurements; simple/complex hard ceilings are 45/120 seconds and p95 targets are
30/90 seconds. Timeout and verification rejection cannot count as correct abstention.

Collect source reviews, labels and observed report evidence before requesting a
production receipt. Restart changed API/worker processes and apply the additive
migration before runtime acceptance; generated contracts or offline fixtures do not
establish acceptance of the live corpus.


### First activation, comparison and rollback

Production first-build certification uses comparison_mode=first_build, a null
compared_active_build_id, no active pointer or prior activation history, and all
repeated candidate source/Message/parity/timing proof. The report records an absent
baseline explicitly. Production definitions still require comparison evidence;
subsequent builds must capture the actual distinct current active build.

Authentic failing active observations remain comparison-only metrics. They cannot
supply candidate release cases; every candidate repetition still must pass its
reviewed label, assertion proof, complete-turn ceilings and p95 targets. Baseline
failures and latency overruns are preserved as observations.

Rollback validates the exact retained previous build's original production receipt
and observed report against current code, runtime/configuration, source generation,
manifest, structure, embedding and approved-set identities. Historical comparison
identity must match that receipt/report, but is not rebound to the later active build.
A stale identity or an unavailable/incompatible embedder still blocks the pointer move.
