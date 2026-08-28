import { describe, expect, test } from "vitest";
import type { SourceRevision } from "../../api/operatorApiClient";
import {
  buildSourceMetadataCorrection,
  sourceMetadataDraftFromRevision,
} from "./sourceUploadMetadata";

const current: SourceRevision = {
  id: "11111111-1111-1111-1111-111111111111",
  project_id: "project-1",
  document_id: "document-1",
  source_group_id: "group-current",
  revision_number: 1,
  revision_label: "Initial",
  title: "Current source",
  source_type: "Act",
  published_date: "2023-01-01",
  effective_from: "2023-01-01",
  effective_to: null,
  lifecycle_status: "active",
  source_role: "primary",
  change_reason: "Initial",
  created_by: "operator",
  content_hash: "a".repeat(64),
  created_at: "2026-08-29T00:00:00Z",
  relationships: [],
  warnings: [],
};

const target: SourceRevision = {
  ...current,
  id: "22222222-2222-2222-2222-222222222222",
  document_id: "document-2",
  source_group_id: "group-target",
  revision_number: 4,
  title: "Target source",
};

describe("buildSourceMetadataCorrection", () => {
  test("keeps a metadata-only correction in the current group", () => {
    const result = buildSourceMetadataCorrection({
      current,
      treatment: "keep",
      draft: sourceMetadataDraftFromRevision(current),
    });

    expect(result).toMatchObject({
      activate: true,
      create_new_group: false,
      revision_label: "Revision 2",
      relationships: [],
    });
    expect(result).not.toHaveProperty("source_group_id");
  });

  test("moves an accidentally independent upload into the target source history", () => {
    const result = buildSourceMetadataCorrection({
      current,
      target,
      treatment: "revision",
      draft: { ...sourceMetadataDraftFromRevision(current), changeReason: "Wrong upload choice" },
    });

    expect(result).toMatchObject({
      activate: true,
      create_new_group: false,
      source_group_id: "group-target",
      revision_label: "Revision 5",
      change_reason: "Wrong upload choice",
      relationships: [{ relationship_type: "replaces", target_revision_id: target.id }],
    });
  });

  test("keeps a modifying source separate while recording its target", () => {
    const result = buildSourceMetadataCorrection({
      current,
      target,
      treatment: "modifies",
      draft: sourceMetadataDraftFromRevision(current),
    });

    expect(result).toMatchObject({
      create_new_group: true,
      relationships: [{ relationship_type: "modifies", target_revision_id: target.id }],
    });
    expect(result).not.toHaveProperty("source_group_id");
  });
});
