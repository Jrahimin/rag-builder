# Correction history and effect proof


## Correction history and effect proof

Latest administrative correction at a pinned generation is selected within each explicitly declared edition before legal interval filtering. Without explicit edition identity, historical lookup uses latest activation and never revives obsolete null-date metadata. Relationship writes optionally carry typed provision_effect, replacement_scope_verified and supporting_spans. Verified effects require exact quotations/offsets against project-scoped modifier chunks. Unknown effects remain unresolved and never delete base provisions. Reprocessing creates source-attested scope candidates and structure.v1 bundles; filename/title inference cannot establish applicability.


## Legal period identity and replacement

Assessment-year and fiscal-year labels are typed identities: AY2025-26 differs from
AY2026-27 and FY2025-26 even when endpoints touch or year numbers match.
A label establishes an interval only when its source-attested fact explicitly
declares period_mode=range. An explicit request spanning multiple legal periods
preserves a prior edition unless an applicable replacement covers every requested
period. Coverage can be attested in separate pinned chunks of the same replacing
document; it need not occur in one chunk row. Project, build and source-generation
restrictions still apply.

Legal applicability and availability at a known-at cutoff are separate filters.
A retrospective publication cannot be used before it was available. “Known before”
uses an exclusive publication cutoff; “available on” uses an inclusive cutoff.
Administrative activation selection remains separate from legal edition identity.
Source-attested candidates, including OCR-derived ranges, are not automatically
validated legal facts.

The post-QA source dossier leaves historical AY2025-26 applicability and the office-rent
amendment chain unresolved. Consolidated Section109 text alone does not certify the
SRO273 effect. This implementation makes no production metadata corrections, rebuilds
or index activations. Completing that source governance requires a separate authorized
and source-backed operation.


## Versioned scope proof and offline reconciliation

New chunks carry `scope_fact_version=scope.v2` alongside the unchanged structural
container version. Period facts record mention/governing scope, locality
(document/provision/table/span), modality (unknown/operative/proposal/example),
exact source spans and review provenance. Plural period lists are separate local
facts. Extraction establishes mentions; it does not establish governing periods,
enactment or exhaustive document coverage.

Retrieval keeps unknown, legacy, local and proposal facts eligible. A period hard
exclusion requires reviewed, exhaustive, operative document scope with compatible
period kinds. A local table year cannot exclude the rest of its document.
Publication dates never become effective dates. Existing immutable revisions and
configuration snapshots are not rewritten.

`source.reconciliation.v1` plans retain project/document/revision/build/chunk IDs,
source and quote hashes, offsets and reviewer provenance. Unchanged source text
can be re-attested; changed text/structure requires private reprocessing; missing
schedules/footnotes require reacquisition/reparse. Unavailable replacement effects
and governing periods remain named obligations. Plans do not execute those actions.

The captured Budget Speech proof comes from the SELECT export of chunks 180/181
on its saved immutable build. It supports proposal statements and period rows;
it does not establish effective dates or enactment. This offline implementation
has not reconciled the live corpus, reparsed any source, or certified a candidate.

### Canonical scope publication and SQL consumption

The complete `scope.v2` envelope is required at both publication and consumption.
Missing/mismatched outer or per-fact versions, malformed reviewer/hash/span values,
or noncanonical fact fields stay unknown for retrieval eligibility and cannot
exclude a document. Semantic publication rejects malformed asserted v2 provenance.
The additive SQL validator enforces the same version, field, type, quote and review
requirements before a governing period participates in an exclusion query.

### Explicit edition identity

Immutable source revisions optionally declare `edition_key`. This is separate from
`source_group_id` (replacement history) and `work_key` (reprints/translations).
Historical lookup first selects the latest administrative correction within each
declared edition, then selects the applicable edition at the pinned generation.
Dates and revision labels alone never declare an edition. Omitted keys inherit
within the active group; explicit null restores latest-only legacy selection.
A null current identity cannot resurrect older declared or null-date metadata.
Current lookup retains latest activation semantics. Additive migration 0041 adds
only the nullable identity column; applied 0039/0040 remain unchanged.

### Cross-kind exhaustive inventories

Presence of a validated, reviewed, exhaustive, operative document-period inventory
is independent of the requested legal kind. An FY-only inventory affirmatively
excludes an AY request, and vice versa; matching year numbers do not convert one
identity into another. Exact kind/year/range matching is unchanged. Plural-period
recall may retain independently matching sources, but a replacing document must
cover every requested period before suppressing an older source.

Neutral/unspecified source lifecycle still stays eligible. Mentions, local
scope, unreviewed/nonexhaustive/proposal/example/unknown effects, malformed
provenance and untyped periods do not establish an exhaustive inventory and
cannot prove whole-document incompatibility. Applied validators and migrations
are unchanged; the correction separates inventory presence from matching.

### Immutable build scope reviews

Super Admin publication at `POST /api/v1/projects/{project_id}/index-builds/{build_id}/scope-reviews`
attaches a reviewed `scope.v2` overlay to one sealed, unactivated `validated` candidate.
The request binds the Project, build, final chunk hash, current source generation,
source revision and source content hash. Every reviewed fact supplies an exact quote,
document character offsets, quote hash and review reason; the authenticated reviewer
is recorded by the service. Typed period/provision claims must occur in that span.

Publication locks the candidate and verifies indexed membership and the pinned source
revision. An identical review is idempotent; changing a published review requires a new
private candidate. The overlay does not rewrite parsed chunks or sealed index rows.
Retrieval hydrates it only for its pinned build, and scope review hashes participate in
semantic snapshot, evaluation and Message acceptance identity. Extraction alone still
cannot publish governing, exhaustive or operative scope.

Additive migration `20261003_0042` creates immutable `index_scope_reviews` and
`index_acceptance_reports`; previous migrations and historical snapshots remain intact.
