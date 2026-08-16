# Operator onboarding and source lifecycle

## Purpose

Phase 2 completes the Operator workflow while preserving the platform boundaries: Organization is
the client and machine-to-machine credential boundary; Project is the knowledge, configuration,
and execution boundary. Project AI-policy writes remain restricted to Super Admin Operators.

## Operator workflow

The Organizations workspace supports create, detail, edit, disable, archive, restore, associated
Projects, and complete credential management. A newly created or replacement secret appears in a
single handoff panel with the API origin, supported header form, copy action, and local download.
The browser does not put the secret in persistent storage. Replacement-first rotation leaves the
old key valid until an explicit revoke; emergency replace-and-revoke requires confirmation.

The canonical Projects workspace uses `/api/v1/operator/projects`. Creation always selects an
Organization. Legacy default-Organization Projects expose dependency counts before either reassignment
or confirmation locks ownership. Project detail owns lifecycle, audit history, AI-policy revisions,
documents, upload, and source metadata. The Test Lab cannot create Projects.

Restoring an archived Organization or Project leaves it disabled. This prevents an archive/restore
operation from silently re-enabling clients or workloads.

## Source metadata architecture

Knowledge owns three append-only concepts:

- immutable source metadata revisions, including lifecycle/role/effective interval/group;
- validated immutable `replaces` and `modifies` relationships;
- activation events that increment a Project-wide source metadata generation under a Project row lock.

Every uploaded Document receives an initial active revision. Uploaders may provide optional
`source_metadata` JSON; omission produces neutral defaults. Activation history can reconstruct the
state at any generation and is independent of parsing/indexing `document.version`. Audit events are
written for revision creation and activation.

## Retrieval integration

Retrieval consumes source state through a public read-only contract wired by composition; Knowledge
continues to own source SQL and writes. Both semantic and keyword candidates use the same captured
generation and canonical applicability scope. Current replacement, explicit historical intervals,
modifier separation, same-group consolidation, neutral legacy behavior, and `off / observe /
enforce` rollout are implemented without rebuilding the content index after metadata-only changes.
