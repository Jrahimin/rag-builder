# ADR-015: Immutable retrieval builds with an atomic active pointer

**Status:** Accepted

## Context

The original retrieval pipeline replaced embeddings and keyword rows in place.
A crash, bad configuration, or destructive document change could therefore
leave search reading a partial corpus. `document.version` and
`embedding_set_version` identify inputs but do not identify a complete,
activatable corpus snapshot.

## Decision

Add a minimal Project-scoped `IndexBuild` identity to all vector, keyword, and
term-stat rows, plus one `ProjectIndexPointer` containing active and previous
build IDs.

A worker builds a full snapshot privately, records its exact document/version
manifest and corpus/configuration fingerprints, validates counts and version
fences, and only then seals it as `validated`. Search resolves the pointer and
filters all candidate/stat queries by the active build. It never infers activity
from document state or the newest row.

Activation and rollback lock the pointer and change active, previous, and build
states in one PostgreSQL transaction. Delete activates an excluding snapshot
before soft deletion. Purge additionally removes every artifact and invalidates
retained snapshots that referenced the document.

## Consequences

- Partial and failed work cannot affect live retrieval.
- Activation and rollback are fast metadata transactions.
- Full corpus builds consume additional compute and temporarily duplicate index
  storage; this is accepted for correctness at the current deployment scale.
- A retained snapshot is the single rollback target. Broader retention and
  garbage collection are intentionally deferred.
- PostgreSQL remains both retrieval store and activation authority; no second
  index control plane is introduced.

## Alternatives considered

- **Continue replacing rows in place:** rejected because transaction boundaries
  do not span provider calls and cannot provide a safe rollback target.
- **Switch table/schema names per build:** rejected as heavier operational
  machinery than a build foreign key and pointer require.
- **Use document status or latest timestamp:** rejected because neither proves a
  complete vector+keyword snapshot.
