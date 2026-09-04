/* eslint-disable react-refresh/only-export-components -- helpers shared by Project forms */
import { CircleHelp } from "lucide-react";
import { useState, type Dispatch, type ReactNode, type SetStateAction } from "react";
import type {
  ActiveConfiguration,
  EffectiveProjectAIConfig,
  GenerationModelOption,
  ProjectAIConfig,
  RAGProfileOption,
} from "../../api/operatorApiClient";

export type TranslationMode = "inherit" | "enabled" | "disabled";
export type RerankModeChoice = "inherit" | "always" | "cross_language";
export type ResponseModeChoice = "indexed_only" | "indexed_then_web" | "indexed_and_web";
export type GroundingAssurance = "inherit" | "strict" | "balanced";

export type ProjectConfigForm = {
  profileId: string;
  customExecution: Record<string, unknown>;
  generationModelId: string;
  responseMode: ResponseModeChoice;
  groundingAssurance: GroundingAssurance;
  domain: string;
  translation: TranslationMode;
  rerankMode: RerankModeChoice;
  topK: string;
  reason: string;
};

export type ProjectConfigOverride = Exclude<
  keyof ProjectConfigForm,
  "reason" | "translation" | "rerankMode" | "customExecution"
>;
export type ProjectConfigOverrides = Record<ProjectConfigOverride, boolean>;

export const inheritedProjectConfig: ProjectConfigOverrides = {
  profileId: false,
  generationModelId: false,
  responseMode: false,
  groundingAssurance: false,
  domain: false,
  topK: false,
};

export const emptyProjectConfigForm: ProjectConfigForm = {
  profileId: "inherit",
  customExecution: {},
  generationModelId: "",
  responseMode: "indexed_only",
  groundingAssurance: "inherit",
  domain: "",
  translation: "inherit",
  rerankMode: "inherit",
  topK: "",
  reason: "",
};

function hasValue(value: object | undefined, key: string) {
  return value !== undefined && Object.prototype.hasOwnProperty.call(value, key);
}

export function storedTranslationMode(stored: ProjectAIConfig): TranslationMode {
  return stored.behavior?.translation_policy ?? "inherit";
}

export function storedRerankMode(stored: ProjectAIConfig): RerankModeChoice {
  return stored.execution?.rerank_mode ?? "inherit";
}

export function configFormFromEffective(
  config: EffectiveProjectAIConfig,
  stored: ProjectAIConfig = {},
): ProjectConfigForm {
  const configuration = config.configuration;
  return {
    ...emptyProjectConfigForm,
    profileId: stored.execution?.profile_id ?? "inherit",
    customExecution: Object.fromEntries(
      Object.entries(stored.execution ?? {}).filter(([key]) => key !== "profile_id"),
    ),
    generationModelId: configuration.llm.generation_model_id ?? "",
    responseMode: configuration.chat.response_mode,
    groundingAssurance: stored.behavior?.grounding_assurance ?? "inherit",
    domain: configuration.domain_instructions,
    translation: storedTranslationMode(stored),
    rerankMode: storedRerankMode(stored),
    topK: String(configuration.retrieval.top_k),
    reason: "",
  };
}

export function configOverridesFromStored(stored: ProjectAIConfig): ProjectConfigOverrides {
  return {
    ...inheritedProjectConfig,
    profileId:
      hasValue(stored.execution, "profile_id") && stored.execution?.profile_id !== "inherit",
    generationModelId: hasValue(stored.behavior, "generation_model_id"),
    responseMode: hasValue(stored.behavior, "response_mode"),
    groundingAssurance: hasValue(stored.behavior, "grounding_assurance"),
    domain: hasValue(stored.behavior, "domain_instructions"),
    topK: hasValue(stored.execution, "retrieval_top_k"),
  };
}

function setSparseValue(
  target: Record<string, unknown>,
  key: string,
  include: boolean,
  value: unknown,
) {
  if (include) target[key] = value;
  else delete target[key];
}

export function buildSparseProjectConfig(
  stored: ProjectAIConfig,
  form: ProjectConfigForm,
  overrides: ProjectConfigOverrides,
): ProjectAIConfig {
  const behavior = { ...(stored.behavior ?? {}) } as Record<string, unknown>;
  const execution: Record<string, unknown> = {};
  if (form.profileId && form.profileId !== "inherit") execution.profile_id = form.profileId;
  setSparseValue(
    behavior,
    "generation_model_id",
    overrides.generationModelId,
    form.generationModelId,
  );
  setSparseValue(behavior, "response_mode", overrides.responseMode, form.responseMode);
  setSparseValue(
    behavior,
    "grounding_assurance",
    overrides.groundingAssurance && form.groundingAssurance !== "inherit",
    form.groundingAssurance,
  );
  setSparseValue(behavior, "domain_instructions", overrides.domain, form.domain);
  if (form.translation === "inherit") delete behavior.translation_policy;
  else behavior.translation_policy = form.translation;
  if (form.profileId === "custom") {
    Object.assign(execution, form.customExecution);
    if (form.rerankMode === "inherit") delete execution.rerank_mode;
    else execution.rerank_mode = form.rerankMode;
    execution.retrieval_top_k = Number(form.topK);
  }
  return { behavior, execution } as ProjectAIConfig;
}

export function sparseHasOverrides(configuration: ProjectAIConfig): boolean {
  const behavior = { ...(configuration.behavior ?? {}) } as Record<string, unknown>;
  const execution = (configuration.execution ?? {}) as Record<string, unknown>;
  return (
    Object.keys(behavior).length > 0 ||
    (execution.profile_id !== undefined && execution.profile_id !== "inherit") ||
    Object.keys(execution).some((key) => key !== "profile_id")
  );
}

export function inheritedFormFromEffective(
  effective?: EffectiveProjectAIConfig | null,
): ProjectConfigForm {
  return effective ? configFormFromEffective(effective) : { ...emptyProjectConfigForm };
}

export function configFormFromDeployment(config: ActiveConfiguration): ProjectConfigForm {
  return {
    ...emptyProjectConfigForm,
    responseMode: config.chat_response_mode as ResponseModeChoice,
  };
}

export function deploymentFormBaseline(
  deployment?: EffectiveProjectAIConfig["configuration"] | null,
  fallback?: ProjectConfigForm,
): ProjectConfigForm {
  if (!deployment) return fallback ?? emptyProjectConfigForm;
  return {
    ...emptyProjectConfigForm,
    generationModelId: deployment.llm.generation_model_id ?? "",
    responseMode: deployment.chat.response_mode,
    groundingAssurance: deployment.chat.grounding_mode === "balanced" ? "balanced" : "strict",
    domain: deployment.domain_instructions ?? "",
    topK: String(deployment.retrieval.top_k),
  };
}

export function valueMatchesDeployment<K extends ProjectConfigOverride>(
  key: K,
  value: ProjectConfigForm[K],
  baseline: ProjectConfigForm,
): boolean {
  if (key === "generationModelId") {
    return value === "" || value === baseline.generationModelId;
  }
  if (key === "groundingAssurance") {
    return value === "inherit" || value === baseline.groundingAssurance;
  }
  if (key === "profileId") {
    return value === "inherit";
  }
  return value === baseline[key];
}

export function inheritedDisplayValue<K extends ProjectConfigOverride>(
  key: K,
  baseline: ProjectConfigForm,
): ProjectConfigForm[K] {
  if (key === "groundingAssurance") return "inherit" as ProjectConfigForm[K];
  if (key === "profileId") return "inherit" as ProjectConfigForm[K];
  return baseline[key];
}

export function FieldHint({ label, text }: { label: string; text: string }) {
  const [open, setOpen] = useState(false);
  return (
    <span className="field-label">
      {label}
      <button
        type="button"
        className={open ? "field-help field-help--open" : "field-help"}
        aria-expanded={open}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
      >
        <CircleHelp size={13} aria-hidden="true" />
        <span className="sr-only">About {label}</span>
        <span className="field-help__tip" role="tooltip">
          {text}
        </span>
      </button>
    </span>
  );
}

export function InheritanceToggle({
  field,
  overridden,
  onChange,
  disabled = false,
}: {
  field: string;
  overridden: boolean;
  onChange: (overridden: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <label className="inherit-switch">
      <input
        aria-label={`${field}: inherit global`}
        type="checkbox"
        checked={!overridden}
        disabled={disabled}
        onChange={(event) => onChange(!event.target.checked)}
      />
      Inherit global
    </label>
  );
}

export const PROJECT_AI_FIELD_HINTS = {
  generationModel:
    "Choose only a deployment-approved logical generation model. Provider, credentials, and raw model strings remain deployment-owned.",
  responseMode: "Choose indexed-only, fallback web, or indexed-and-web evidence behavior.",
  grounding: "Strict is the deployment baseline. Balanced is a bounded Project behavior choice.",
  domain: "Project-specific standing instructions. This is not a full system prompt.",
  translation: "Translation is off by default. Enable it only for cross-language retrieval.",
  rerank:
    "Hosted reranking remains enabled. Projects may choose always or cross-language, never disable it.",
  topK: "How many passages enter the answer context. This is a canonical advanced execution override.",
  profile:
    "Choose an exact code-owned RAG execution bundle. Certification reports measured validation; it does not block development selection.",
  reason: "A concise audit reason for this immutable revision.",
};

function materializeEffectiveExecution(
  effective?: EffectiveProjectAIConfig | null,
): Record<string, unknown> {
  if (!effective) return {};
  const { retrieval, chat } = effective.configuration;
  return {
    retrieval_top_k: retrieval.top_k,
    semantic_candidate_top_k: retrieval.semantic_candidate_top_k,
    keyword_candidate_top_k: retrieval.keyword_candidate_top_k,
    hnsw_ef_search: retrieval.hnsw_ef_search,
    rrf_k: retrieval.rrf_k,
    semantic_weight: retrieval.semantic_weight,
    keyword_weight: retrieval.keyword_weight,
    score_threshold: retrieval.score_threshold,
    rerank_mode: retrieval.rerank_mode,
    rerank_candidate_window: retrieval.rerank_candidate_window,
    rerank_score_threshold: retrieval.rerank_score_threshold,
    min_ocr_confidence: retrieval.min_ocr_confidence,
    max_chunks_per_document: retrieval.max_chunks_per_document,
    max_chunks_per_section: retrieval.max_chunks_per_section,
    deduplicate_by_content_hash: retrieval.deduplicate_by_content_hash,
    passage_scoring_enabled: retrieval.passage_scoring_enabled,
    passage_window_tokens: retrieval.passage_window_tokens,
    passage_overlap_tokens: retrieval.passage_overlap_tokens,
    passage_min_tokens: retrieval.passage_min_tokens,
    max_related_sources: retrieval.max_related_sources,
    max_relationship_candidates: retrieval.max_relationship_candidates,
    max_context_chunks: chat.max_context_chunks,
    context_char_budget: chat.context_char_budget,
    max_history_messages: chat.max_history_messages,
  };
}

function SettingsField({
  className,
  label,
  hint,
  inherit,
  meta,
  children,
}: {
  className?: string;
  label: string;
  hint: string;
  inherit?: ReactNode;
  meta?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className={className ?? "field-control"}>
      <div className="field-control__header">
        <FieldHint label={label} text={hint} />
        {inherit}
      </div>
      {children}
      {meta}
    </div>
  );
}

export function ProjectAISettingsFields({
  form,
  setForm,
  overrides,
  setOverride,
  effective,
  defaults,
  deploymentConfiguration,
  allowedGenerationModels = [],
  ragProfiles = [],
}: {
  form: ProjectConfigForm;
  setForm: Dispatch<SetStateAction<ProjectConfigForm>>;
  overrides: ProjectConfigOverrides;
  setOverride: (key: ProjectConfigOverride, enabled: boolean) => void;
  effective?: EffectiveProjectAIConfig | null;
  deploymentConfiguration?: EffectiveProjectAIConfig["configuration"] | null;
  defaults?: ProjectConfigForm;
  allowedGenerationModels?: GenerationModelOption[];
  ragProfiles?: RAGProfileOption[];
}) {
  const baseline = deploymentFormBaseline(
    deploymentConfiguration ?? effective?.deployment_configuration ?? null,
    defaults ?? emptyProjectConfigForm,
  );
  const changeField = <K extends ProjectConfigOverride>(key: K, value: ProjectConfigForm[K]) => {
    setForm((current) => ({ ...current, [key]: value }));
    setOverride(key, !valueMatchesDeployment(key, value, baseline));
  };
  const selectProfile = (profileId: string) => {
    const profile = ragProfiles.find((item) => item.id === profileId);
    const currentPresetValues =
      ragProfiles.find((item) => item.id === form.profileId)?.values ?? {};
    const customExecution =
      profileId === "custom"
        ? {
            ...materializeEffectiveExecution(effective),
            ...currentPresetValues,
            ...(form.profileId === "custom" ? form.customExecution : {}),
          }
        : {};
    setForm((current) => ({
      ...current,
      profileId,
      customExecution,
      topK:
        profileId === "custom" && typeof customExecution.retrieval_top_k === "number"
          ? String(customExecution.retrieval_top_k)
          : typeof profile?.values.retrieval_top_k === "number"
            ? String(profile.values.retrieval_top_k)
            : baseline.topK,
      rerankMode:
        profileId === "custom" && typeof customExecution.rerank_mode === "string"
          ? (customExecution.rerank_mode as RerankModeChoice)
          : "inherit",
    }));
    setOverride("profileId", profileId !== "inherit");
    setOverride("topK", false);
  };
  const activateCustom = () => {
    if (form.profileId === "custom") return;
    const profileValues = ragProfiles.find((item) => item.id === form.profileId)?.values ?? {};
    setForm((current) => ({
      ...current,
      profileId: "custom",
      customExecution: {
        ...materializeEffectiveExecution(effective),
        ...profileValues,
      },
    }));
    setOverride("profileId", true);
  };
  const changeTopK = (value: string) => {
    activateCustom();
    setForm((current) => ({
      ...current,
      profileId: "custom",
      topK: value,
      customExecution: { ...current.customExecution, retrieval_top_k: Number(value) },
    }));
    setOverride("topK", !valueMatchesDeployment("topK", value, baseline));
  };
  const changeRerankMode = (value: RerankModeChoice) => {
    activateCustom();
    setForm((current) => ({
      ...current,
      profileId: "custom",
      rerankMode: value,
      customExecution: { ...current.customExecution, rerank_mode: value },
    }));
  };
  const customProfile = form.profileId === "custom";
  const toggleField = (key: ProjectConfigOverride, overridden: boolean) => {
    if (!overridden) {
      const restored = inheritedDisplayValue(key, baseline);
      setForm((current) => ({ ...current, [key]: restored }));
      setOverride(key, false);
      return;
    }
    setOverride(key, true);
  };
  const inheritedClass = (overridden: boolean) =>
    overridden ? "field-control" : "field-control field-control--inherited";
  return (
    <>
      <div className="form-grid ai-settings-grid">
        <SettingsField
          className={inheritedClass(overrides.profileId)}
          label="RAG profile"
          hint={PROJECT_AI_FIELD_HINTS.profile}
          meta={
            form.profileId !== "inherit" ? (
              <span className="field-control__meta" data-testid="rag-profile-state">
                {customProfile
                  ? "Custom — individual execution settings"
                  : `${form.profileId} profile`}
              </span>
            ) : (
              <span className="field-control__meta" aria-hidden="true" />
            )
          }
        >
          <select
            aria-label="RAG profile"
            value={form.profileId}
            onChange={(event) => selectProfile(event.target.value)}
          >
            <option value="inherit">Inherit global profile</option>
            {ragProfiles.map((profile) => (
              <option key={profile.id} value={profile.id} disabled={!profile.selectable}>
                {profile.id} — {profile.certification_status}
              </option>
            ))}
            <option value="custom">Custom</option>
          </select>
        </SettingsField>
        <SettingsField
          className={inheritedClass(overrides.generationModelId)}
          label="Generation model"
          hint={PROJECT_AI_FIELD_HINTS.generationModel}
          inherit={
            <InheritanceToggle
              field="Generation model"
              overridden={overrides.generationModelId}
              onChange={(enabled) => toggleField("generationModelId", enabled)}
            />
          }
          meta={<span className="field-control__meta" aria-hidden="true" />}
        >
          <select
            aria-label="Generation model"
            value={form.generationModelId}
            onChange={(event) => changeField("generationModelId", event.target.value)}
          >
            <option value="">Deployment default</option>
            {allowedGenerationModels.map((model) => (
              <option key={model.id} value={model.id}>
                {model.id} — {model.provider}/{model.model}
              </option>
            ))}
          </select>
        </SettingsField>
        <SettingsField
          className={inheritedClass(overrides.responseMode)}
          label="Response mode"
          hint={PROJECT_AI_FIELD_HINTS.responseMode}
          inherit={
            <InheritanceToggle
              field="Response mode"
              overridden={overrides.responseMode}
              onChange={(enabled) => toggleField("responseMode", enabled)}
            />
          }
        >
          <select
            aria-label="Response mode"
            value={form.responseMode}
            onChange={(event) =>
              changeField("responseMode", event.target.value as ResponseModeChoice)
            }
          >
            <option value="indexed_only">Indexed only</option>
            <option value="indexed_then_web">Indexed, then web</option>
            <option value="indexed_and_web">Indexed and web</option>
          </select>
        </SettingsField>
        <SettingsField
          className={inheritedClass(overrides.groundingAssurance)}
          label="Grounding assurance"
          hint={PROJECT_AI_FIELD_HINTS.grounding}
          inherit={
            <InheritanceToggle
              field="Grounding assurance"
              overridden={overrides.groundingAssurance}
              onChange={(enabled) => toggleField("groundingAssurance", enabled)}
            />
          }
        >
          <select
            aria-label="Grounding assurance"
            value={form.groundingAssurance}
            onChange={(event) =>
              changeField("groundingAssurance", event.target.value as GroundingAssurance)
            }
          >
            <option value="inherit">Deployment default</option>
            <option value="strict">Strict</option>
            <option value="balanced">Balanced</option>
          </select>
        </SettingsField>
        <SettingsField label="Query translation" hint={PROJECT_AI_FIELD_HINTS.translation}>
          <select
            aria-label="Query translation"
            value={form.translation}
            onChange={(event) =>
              setForm((current) => ({
                ...current,
                translation: event.target.value as TranslationMode,
              }))
            }
          >
            <option value="inherit">Inherit deployment default</option>
            <option value="disabled">Off</option>
            <option value="enabled">On</option>
          </select>
        </SettingsField>
        <SettingsField
          className={`${inheritedClass(overrides.domain)} field-control--wide`}
          label="Domain instructions"
          hint={PROJECT_AI_FIELD_HINTS.domain}
          inherit={
            <InheritanceToggle
              field="Domain instructions"
              overridden={overrides.domain}
              onChange={(enabled) => toggleField("domain", enabled)}
            />
          }
        >
          <textarea
            aria-label="Domain instructions"
            rows={4}
            value={form.domain}
            onChange={(event) => changeField("domain", event.target.value)}
          />
        </SettingsField>
      </div>
      <details className="config-advanced">
        <summary>
          Advanced execution controls {customProfile ? "" : "— editing uses Custom"}
        </summary>
        <div className="form-grid">
          <SettingsField label="Rerank mode" hint={PROJECT_AI_FIELD_HINTS.rerank}>
            <select
              aria-label="Rerank mode"
              value={form.rerankMode}
              onChange={(event) => changeRerankMode(event.target.value as RerankModeChoice)}
            >
              <option value="inherit">Inherit deployment default</option>
              <option value="always">Always</option>
              <option value="cross_language">Cross-language</option>
            </select>
          </SettingsField>
          <SettingsField
            className={inheritedClass(overrides.topK)}
            label="Top K"
            hint={PROJECT_AI_FIELD_HINTS.topK}
          >
            <input
              aria-label="Top K"
              type="number"
              min="1"
              max="100"
              value={form.topK}
              onChange={(event) => changeTopK(event.target.value)}
            />
          </SettingsField>
        </div>
      </details>
    </>
  );
}
