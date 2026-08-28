import type { SourceRevision, SourceRevisionCreate } from "../../api/operatorApiClient";

export type SourceUploadMode = "independent" | "revision" | "modifies";
export type SourceCorrectionTreatment = "keep" | SourceUploadMode;

export type SourceMetadataDraft = {
  title: string;
  sourceType: string;
  lifecycle: "unspecified" | "draft" | "active" | "retired";
  role: "unspecified" | "primary" | "supporting" | "reference";
  publishedDate: string;
  effectiveFrom: string;
  effectiveTo: string;
  changeReason: string;
};

type SourceUploadMetadataOptions = {
  filename: string;
  mode: SourceUploadMode;
  target?: SourceRevision;
  draft: SourceMetadataDraft;
  defaultReason: string;
};

export function hasInvalidEffectiveInterval(draft: SourceMetadataDraft) {
  return Boolean(
    draft.effectiveFrom && draft.effectiveTo && draft.effectiveTo < draft.effectiveFrom,
  );
}

export function sourceMetadataDraftFromRevision(revision: SourceRevision): SourceMetadataDraft {
  return {
    title: revision.title,
    sourceType: revision.source_type ?? "",
    lifecycle: revision.lifecycle_status,
    role: revision.source_role,
    publishedDate: revision.published_date?.slice(0, 10) ?? "",
    effectiveFrom: revision.effective_from?.slice(0, 10) ?? "",
    effectiveTo: revision.effective_to?.slice(0, 10) ?? "",
    changeReason: "",
  };
}

/** Build optional source metadata consistently for every document-upload surface. */
export function buildSourceUploadMetadata({
  filename,
  mode,
  target,
  draft,
  defaultReason,
}: SourceUploadMetadataOptions): SourceRevisionCreate | undefined {
  const title = draft.title.trim();
  const sourceType = draft.sourceType.trim();
  const changeReason = draft.changeReason.trim();
  const configured =
    mode !== "independent" ||
    Boolean(
      title || sourceType || draft.publishedDate || draft.effectiveFrom || draft.effectiveTo,
    ) ||
    Boolean(changeReason) ||
    draft.lifecycle !== "active" ||
    draft.role !== "primary";

  if (!configured || (mode !== "independent" && !target)) return undefined;

  return {
    activate: true,
    change_reason: changeReason || defaultReason,
    create_new_group: mode !== "revision",
    ...(mode === "revision" && target ? { source_group_id: target.source_group_id } : {}),
    lifecycle_status: draft.lifecycle,
    revision_label:
      mode === "revision" && target ? `Revision ${target.revision_number + 1}` : "Initial",
    source_role: draft.role,
    source_type: sourceType || null,
    title: title || filename,
    published_date: draft.publishedDate || null,
    effective_from: draft.effectiveFrom || null,
    effective_to: draft.effectiveTo || null,
    relationships:
      mode === "independent" || !target
        ? []
        : [
            {
              relationship_type: mode === "revision" ? "replaces" : "modifies",
              target_revision_id: target.id,
            },
          ],
  };
}

type SourceMetadataCorrectionOptions = {
  current: SourceRevision;
  treatment: SourceCorrectionTreatment;
  target?: SourceRevision;
  draft: SourceMetadataDraft;
};

/**
 * Metadata is append-only. A correction activates a new revision while retaining the
 * original decision in history. It deliberately never schedules document processing.
 */
export function buildSourceMetadataCorrection({
  current,
  treatment,
  target,
  draft,
}: SourceMetadataCorrectionOptions): SourceRevisionCreate | undefined {
  if ((treatment === "revision" || treatment === "modifies") && !target) return undefined;

  const title = draft.title.trim();
  const sourceType = draft.sourceType.trim();
  const changeReason = draft.changeReason.trim();
  const joinsExistingGroup = treatment === "revision" && target;
  const createsSeparateGroup = treatment === "independent" || treatment === "modifies";
  const baseRevisionNumber = createsSeparateGroup
    ? 0
    : joinsExistingGroup
      ? target.revision_number
      : current.revision_number;

  return {
    activate: true,
    ...(changeReason ? { change_reason: changeReason } : {}),
    create_new_group: createsSeparateGroup,
    ...(joinsExistingGroup ? { source_group_id: target.source_group_id } : {}),
    lifecycle_status: draft.lifecycle,
    revision_label: `Revision ${baseRevisionNumber + 1}`,
    source_role: draft.role,
    source_type: sourceType || null,
    title: title || current.title,
    published_date: draft.publishedDate || null,
    effective_from: draft.effectiveFrom || null,
    effective_to: draft.effectiveTo || null,
    relationships:
      treatment === "revision" && target
        ? [{ relationship_type: "replaces", target_revision_id: target.id }]
        : treatment === "modifies" && target
          ? [{ relationship_type: "modifies", target_revision_id: target.id }]
          : [],
  };
}
