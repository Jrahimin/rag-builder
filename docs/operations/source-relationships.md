# Source relationships

The application uses two directed relationships. Both are stored as immutable edges on a metadata revision; an activation makes the entire revision and its edge list current together. No new database tables or relationship types are needed for multiple targets.

| Situation | Configuration |
| --- | --- |
| Unrelated source or supporting guidance | Independent source; choose its role and applicable dates |
| Finance Act changes the stored Bangla and English Act texts | One independent amendment with **Modifies** links to both representations |
| Several amendments affect the same Act | Each amendment links to that Act; retain each amendment's dates and operative evidence |
| Complete corrected edition of a document | **Replaces** one previous edition in the same source history |
| Corrected edition of an amendment | **Replaces** its predecessor and retains/reviews all its **Modifies** links |
| A new law changes or repeals provisions across several older laws | Separate source with **Modifies** links to the affected laws; establish the legal effect from its operative text |
| Translation or alternate representation | Separate source history, not a replacement merely because it covers the same law |
| Metadata typo, date, role or target-list correction | Keep the current history, edit the fields/targets, and save a new metadata revision |
| Incorrect relationship to one target | Remove that target; preserve the other links |
| Source incorrectly joined another source's history | Choose independent to detach it, or correct it to a separate amendment |

“Replaces” means complete edition succession in this application's source history. It is not a general-purpose legal repeal or a merge of several histories. “Modifies” keeps the target text available and requires evidence to determine which rules changed. Source role, lifecycle and effective dates are separate from relationships; a newer publication date alone does not grant authority.

## Operator workflow

Projects → Sources and Test Lab → Documents share the same searchable checkbox picker and payload builders. Choose **Modifies one or more sources**, check every affected source, and save/upload. Selected sources are listed with individual Remove buttons. Existing provision scopes are displayed and preserved; adding a broad document link leaves its scope unspecified rather than inventing affected sections.

Selecting a replacement edition preloads the predecessor's modification targets. Review that list before upload: a new edition may add or remove amendments. Ordinary metadata correction retains the source group and all unedited edges. Saved selections reload after activation, even though the correction menu returns to “Keep this source's current treatment.”

Historical target IDs remain pinned, with their titles resolved from the immutable revision endpoint. They are not silently rebound to a newer document with different content. Metadata-only target corrections remain equivalent when document, source group and content hash match. A target from a changed document or different group must be reviewed explicitly.

## Validation and retrieval behavior

- Multiple modification targets are allowed, including multiple representations of one law. Multiple modifiers may target the same base. Chains are allowed; circular modification chains are rejected at activation, including attempts to restore an old revision that would create a cycle.
- Only one replacement predecessor is allowed. Replacement stays in the same history; modification stays outside that history. A document cannot modify itself through an older metadata revision or a newly created group.
- Duplicate edges, two target revisions from the same history, and contradictory replacement/modification declarations are rejected. Cross-project targets are rejected.
- Deleted documents are excluded from current source choices, and new relationship writes targeting them fail clearly. Explicit historical-generation reads remain available. Drafts remain visible as drafts for deliberate setup; they do not become governing evidence merely by being linked.
- Source activation is serialized with the project generation update. Validation, new edges and activation commit together. Failed validation does not switch the active pointer. Obsolete replacement editions do not contribute stale outgoing links to the current graph, even if their metadata was corrected later.
- An applicable complete replacement excludes its older document under enforced source policy even if that document was left active. This follows metadata-only corrections without suppressing the replacing file itself. Historical dates before the replacement's effective date retain the older edition; a deleted replacing document does not suppress the base.
- A modification link does not hide its base. Retrieval still resolves applicable dates, authority and evidence. Missing effective dates and any unscoped modification target produce warnings. Conflicting amendments need operative evidence, not an unconditional “newest file wins” rule.
- Amendment expansion remains bounded and direct; this change does not introduce recursive retrieval or automatic legal consolidation. A chain is not a claim that every downstream rule has been reconstructed. Missing applicable evidence can still require an incomplete-evidence response.
- Source metadata activation and index activation are separate. Metadata-only corrections need no reprocessing; a newly uploaded replacement still needs successful processing and index activation before its content can answer questions. Check processing/index status when a governing replacement is not yet searchable.

## Local verification — 7 September 2026

Project `2ee2756f-ad27-44df-a9d3-1316b10ccbb1` has seven current, nondeleted source records. Finance Act 2026 was saved from the browser's multi-target correction form as metadata revision 4, at source generation **17**, retaining its existing source group and both Act targets. The refreshed editor displays both selections and excludes deleted documents.

The two base representations remain independent primary Acts; Finance remains a primary amendment; Nirdeshika and Paripatra remain supporting guidance/circulars; the budget speech remains reference material; the sample remains a draft reference. No extra relationships were inferred for those documents.

Tests cover both upload/edit surfaces, multi-target persistence, scoped-edge preservation, removal, replacement plus amendments, graph cycles and restoration, cross-project/self/duplicate targets, deleted targets and historical snapshots, and replacement-aware retrieval after metadata correction. Changes are local and require deployment to affect Live.
