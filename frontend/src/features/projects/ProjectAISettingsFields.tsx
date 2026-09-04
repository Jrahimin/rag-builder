/* eslint-disable react-refresh/only-export-components -- helpers shared by Project forms */
import { CircleHelp } from "lucide-react";
import { useState, type Dispatch, type ReactNode, type SetStateAction } from "react";
import type {
  EffectiveProjectAIConfig,
  GenerationModelOption,
  ProjectAIConfig,
  RAGProfileOption,
} from "../../api/operatorApiClient";

export const GLOBAL_SELECTION = "__global__";

export type TranslationMode = "inherit" | "enabled" | "disabled";
export type RerankModeChoice = "always" | "cross_language";
export type ResponseModeChoice =
  "inherit" | "indexed_only" | "indexed_then_web" | "indexed_and_web";
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
  generationModelId: GLOBAL_SELECTION,
  responseMode: "inherit",
  groundingAssurance: "inherit",
  domain: "",
  translation: "inherit",
  rerankMode: "always",
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
  return (stored.execution?.rerank_mode as RerankModeChoice | undefined) ?? "always";
}

export function configFormFromEffective(
  config: EffectiveProjectAIConfig,
  stored: ProjectAIConfig = {},
): ProjectConfigForm {
  const configuration = config.configuration;
  const storedExecution = (stored.execution ?? {}) as Record<string, unknown>;
  const storedProfileId = stored.execution?.profile_id;
  const hasExecutionValues = Object.keys(storedExecution).some((key) => key !== "profile_id");
  const profileId = storedProfileId ?? (hasExecutionValues ? "custom" : "inherit");
  const explicitExecution = Object.fromEntries(
    Object.entries(storedExecution).filter(([key]) => key !== "profile_id"),
  );
  const customExecution =
    profileId === "custom"
      ? { ...materializeEffectiveExecution(config), ...explicitExecution }
      : explicitExecution;
  return {
    ...emptyProjectConfigForm,
    profileId,
    customExecution,
    generationModelId: stored.behavior?.generation_model_id ?? GLOBAL_SELECTION,
    responseMode: stored.behavior?.response_mode ?? "inherit",
    groundingAssurance: stored.behavior?.grounding_assurance ?? "inherit",
    domain: configuration.domain_instructions,
    translation: storedTranslationMode(stored),
    rerankMode:
      profileId === "custom"
        ? ((customExecution.rerank_mode as RerankModeChoice | undefined) ??
          (configuration.retrieval.rerank_mode as RerankModeChoice))
        : (configuration.retrieval.rerank_mode as RerankModeChoice),
    topK: String(configuration.retrieval.top_k),
    reason: "",
  };
}

export function configOverridesFromStored(stored: ProjectAIConfig): ProjectConfigOverrides {
  const hasExecutionValues = Object.keys(stored.execution ?? {}).some(
    (key) => key !== "profile_id",
  );
  return {
    ...inheritedProjectConfig,
    profileId:
      (hasValue(stored.execution, "profile_id") && stored.execution?.profile_id !== "inherit") ||
      hasExecutionValues,
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
    form.generationModelId !== GLOBAL_SELECTION,
    form.generationModelId,
  );
  setSparseValue(behavior, "response_mode", form.responseMode !== "inherit", form.responseMode);
  setSparseValue(
    behavior,
    "grounding_assurance",
    form.groundingAssurance !== "inherit",
    form.groundingAssurance,
  );
  setSparseValue(behavior, "domain_instructions", overrides.domain, form.domain);
  if (form.translation === "inherit") delete behavior.translation_policy;
  else behavior.translation_policy = form.translation;
  if (form.profileId === "custom") {
    Object.assign(execution, form.customExecution);
    execution.rerank_mode = form.rerankMode;
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

export function configFormFromDeployment(): ProjectConfigForm {
  return {
    ...emptyProjectConfigForm,
    responseMode: "inherit",
  };
}

export function deploymentFormBaseline(
  deployment?: EffectiveProjectAIConfig["configuration"] | null,
  fallback?: ProjectConfigForm,
): ProjectConfigForm {
  if (!deployment) return fallback ?? emptyProjectConfigForm;
  return {
    ...emptyProjectConfigForm,
    generationModelId: deployment.llm.generation_model_id ?? GLOBAL_SELECTION,
    responseMode: deployment.chat.response_mode,
    groundingAssurance: deployment.chat.grounding_mode === "balanced" ? "balanced" : "strict",
    domain: deployment.domain_instructions ?? "",
    topK: String(deployment.retrieval.top_k),
  };
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
  return materializeExecutionConfiguration(effective.configuration);
}

function materializeExecutionConfiguration(
  configuration: EffectiveProjectAIConfig["configuration"],
): Record<string, unknown> {
  const { retrieval, chat } = configuration;
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

const RESPONSE_MODE_LABELS: Record<Exclude<ResponseModeChoice, "inherit">, string> = {
  indexed_only: "Indexed only",
  indexed_then_web: "Indexed, then web",
  indexed_and_web: "Indexed and web",
};

const GROUNDING_LABELS: Record<Exclude<GroundingAssurance, "inherit">, string> = {
  strict: "Strict",
  balanced: "Balanced",
};

const PROFILE_INTENTS: Record<string, string> = {
  economy: "Lower cost and latency",
  standard: "Balanced everyday retrieval",
  quality: "Deeper retrieval for best quality",
  custom: "A complete, explicit execution bundle",
};

function displayProfileName(profileId: string) {
  return profileId.charAt(0).toUpperCase() + profileId.slice(1).replaceAll("_", " ");
}

function executionSummary(values: Record<string, unknown>): string {
  const parts: string[] = [];
  const semantic = values.semantic_candidate_top_k;
  const keyword = values.keyword_candidate_top_k;
  if (typeof semantic === "number" || typeof keyword === "number") {
    const semanticLabel = typeof semantic === "number" ? semantic : "—";
    const keywordLabel = typeof keyword === "number" ? keyword : "—";
    parts.push(`Candidates ${semanticLabel}/${keywordLabel}`);
  }
  if (typeof values.rerank_mode === "string") {
    const mode = displayProfileName(values.rerank_mode);
    const window = values.rerank_candidate_window;
    parts.push(`Rerank ${mode}${typeof window === "number" ? ` · ${window}` : ""}`);
  }
  if (typeof values.retrieval_top_k === "number") {
    parts.push(`Top K ${values.retrieval_top_k}`);
  }
  const chunks = values.max_context_chunks;
  const budget = values.context_char_budget;
  if (typeof chunks === "number" || typeof budget === "number") {
    const chunkLabel = typeof chunks === "number" ? chunks : "—";
    parts.push(
      `Context ${chunkLabel} chunks${typeof budget === "number" ? ` · ${budget.toLocaleString()} chars` : ""}`,
    );
  }
  if (typeof values.max_history_messages === "number") {
    parts.push(`History ${values.max_history_messages}`);
  }
  return parts.join(" · ") || "Uses the current effective execution values";
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
  globalRagProfileId,
  deploymentResponseMode,
  deploymentGenerationModelLabel,
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
  globalRagProfileId?: string | null;
  deploymentResponseMode?: ResponseModeChoice;
  deploymentGenerationModelLabel?: string | null;
}) {
  const baseline = deploymentFormBaseline(
    deploymentConfiguration ?? effective?.deployment_configuration ?? null,
    defaults ?? emptyProjectConfigForm,
  );
  const effectiveGlobalProfile =
    globalRagProfileId ??
    effective?.provenance.deployment_default_execution_profile_id ??
    "standard";
  const globalExecution = deploymentConfiguration
    ? materializeExecutionConfiguration(deploymentConfiguration)
    : materializeEffectiveExecution(effective);
  const globalResponseMode =
    deploymentResponseMode && deploymentResponseMode !== "inherit"
      ? deploymentResponseMode
      : baseline.responseMode === "inherit"
        ? "indexed_only"
        : baseline.responseMode;
  const globalGrounding =
    baseline.groundingAssurance === "inherit" ? "strict" : baseline.groundingAssurance;
  const globalModelId =
    deploymentConfiguration?.llm.generation_model_id ??
    (baseline.generationModelId === GLOBAL_SELECTION ? null : baseline.generationModelId);
  const globalModelLabel =
    globalModelId ??
    deploymentConfiguration?.llm.model ??
    deploymentGenerationModelLabel ??
    allowedGenerationModels[0]?.id ??
    "Default model";
  const onlyGlobalModel =
    allowedGenerationModels.length === 1 && allowedGenerationModels[0]?.id === globalModelId;
  const showStaticModel = allowedGenerationModels.length === 0 || onlyGlobalModel;
  const globalTranslationEnabled =
    deploymentConfiguration?.retrieval.query_translation_enabled ??
    effective?.deployment_configuration?.retrieval.query_translation_enabled ??
    false;

  const changeGenerationModel = (value: string) => {
    setForm((current) => ({ ...current, generationModelId: value }));
    setOverride("generationModelId", value !== GLOBAL_SELECTION);
  };
  const changeResponseMode = (value: ResponseModeChoice) => {
    setForm((current) => ({ ...current, responseMode: value }));
    setOverride("responseMode", value !== "inherit");
  };
  const changeGrounding = (value: GroundingAssurance) => {
    setForm((current) => ({ ...current, groundingAssurance: value }));
    setOverride("groundingAssurance", value !== "inherit");
  };
  const selectProfile = (profileId: string) => {
    const profile = ragProfiles.find((item) => item.id === profileId);
    setForm((current) => {
      const selectedPreset =
        ragProfiles.find((item) => item.id === current.profileId)?.values ?? {};
      const currentExecution = {
        ...materializeEffectiveExecution(effective),
        ...selectedPreset,
        ...(current.profileId === "custom" ? current.customExecution : {}),
      };
      const nextExecution =
        profileId === "custom"
          ? currentExecution
          : profileId === "inherit"
            ? globalExecution
            : (profile?.values ?? {});
      return {
        ...current,
        profileId,
        customExecution: profileId === "custom" ? nextExecution : {},
        topK:
          typeof nextExecution.retrieval_top_k === "number"
            ? String(nextExecution.retrieval_top_k)
            : baseline.topK,
        rerankMode: (nextExecution.rerank_mode as RerankModeChoice | undefined) ?? "always",
      };
    });
    setOverride("profileId", profileId !== "inherit");
    setOverride("topK", profileId === "custom");
  };
  const materializeSelectedExecution = (current: ProjectConfigForm) => ({
    ...materializeEffectiveExecution(effective),
    ...(ragProfiles.find((item) => item.id === current.profileId)?.values ?? {}),
    ...(current.profileId === "custom" ? current.customExecution : {}),
  });
  const changeTopK = (value: string) => {
    setForm((current) => ({
      ...current,
      profileId: "custom",
      topK: value,
      customExecution: {
        ...materializeSelectedExecution(current),
        ...(value ? { retrieval_top_k: Number(value) } : {}),
      },
    }));
    setOverride("profileId", true);
    setOverride("topK", true);
  };
  const changeRerankMode = (value: RerankModeChoice) => {
    setForm((current) => ({
      ...current,
      profileId: "custom",
      rerankMode: value,
      customExecution: { ...materializeSelectedExecution(current), rerank_mode: value },
    }));
    setOverride("profileId", true);
  };
  const customProfile = form.profileId === "custom";
  const toggleDomain = (overridden: boolean) => {
    setForm((current) => ({
      ...current,
      domain: overridden ? current.domain : baseline.domain,
    }));
    setOverride("domain", overridden);
  };
  const inheritedClass = (overridden: boolean) =>
    overridden ? "field-control" : "field-control field-control--inherited";
  return (
    <>
      <section className="profile-selector" aria-label="RAG execution profiles">
        <div className="profile-selector__heading">
          <FieldHint label="RAG execution profile" text={PROJECT_AI_FIELD_HINTS.profile} />
          <span className="field-control__meta" data-testid="rag-profile-state">
            {form.profileId === "inherit"
              ? `Global — ${displayProfileName(effectiveGlobalProfile)}`
              : customProfile
                ? "Custom — complete explicit execution"
                : `${displayProfileName(form.profileId)} profile`}
          </span>
        </div>
        <div
          className="profile-selector__cards"
          role="radiogroup"
          aria-label="RAG execution profile"
        >
          <button
            type="button"
            role="radio"
            aria-checked={form.profileId === "inherit"}
            aria-label="Global RAG profile"
            className={
              form.profileId === "inherit" ? "profile-card profile-card--selected" : "profile-card"
            }
            onClick={() => selectProfile("inherit")}
          >
            <span className="profile-card__title">Global</span>
            <span className="profile-card__intent">
              Current: {displayProfileName(effectiveGlobalProfile)}
            </span>
            <span className="profile-card__summary">{executionSummary(globalExecution)}</span>
          </button>
          {ragProfiles
            .filter((profile) => profile.id !== "custom")
            .map((profile) => (
              <button
                key={profile.id}
                type="button"
                role="radio"
                aria-checked={form.profileId === profile.id}
                aria-label={`${displayProfileName(profile.id)} RAG profile`}
                disabled={!profile.selectable && form.profileId !== profile.id}
                className={
                  form.profileId === profile.id
                    ? "profile-card profile-card--selected"
                    : "profile-card"
                }
                onClick={() => selectProfile(profile.id)}
              >
                <span className="profile-card__title">
                  {displayProfileName(profile.id)}
                  {profile.recommended && <small>Recommended</small>}
                </span>
                <span className="profile-card__intent">
                  {PROFILE_INTENTS[profile.id] ?? "Predefined execution template"}
                </span>
                <span className="profile-card__summary">{executionSummary(profile.values)}</span>
              </button>
            ))}
          <button
            type="button"
            role="radio"
            aria-checked={customProfile}
            aria-label="Custom RAG profile"
            disabled={!effective && ragProfiles.length === 0}
            className={customProfile ? "profile-card profile-card--selected" : "profile-card"}
            onClick={() => selectProfile("custom")}
          >
            <span className="profile-card__title">Custom</span>
            <span className="profile-card__intent">{PROFILE_INTENTS.custom}</span>
            <span className="profile-card__summary">
              {customProfile
                ? executionSummary(form.customExecution)
                : "Edit any execution control to start from the selected profile"}
            </span>
          </button>
        </div>
      </section>
      <div className="form-grid ai-settings-grid">
        <SettingsField
          className={inheritedClass(overrides.generationModelId)}
          label="Generation model"
          hint={PROJECT_AI_FIELD_HINTS.generationModel}
          meta={
            showStaticModel ? (
              <span className="field-control__meta">
                {form.generationModelId === GLOBAL_SELECTION
                  ? "Uses Global and follows future deployment changes."
                  : "Pinned to this Project."}
              </span>
            ) : undefined
          }
        >
          {showStaticModel ? (
            <div className="source-value">
              <span>
                <strong>{globalModelLabel}</strong>
                {allowedGenerationModels[0] && (
                  <small>
                    {allowedGenerationModels[0].provider}/{allowedGenerationModels[0].model}
                  </small>
                )}
              </span>
              {onlyGlobalModel && (
                <button
                  className="button button--tertiary"
                  type="button"
                  onClick={() =>
                    changeGenerationModel(
                      form.generationModelId === GLOBAL_SELECTION
                        ? (allowedGenerationModels[0]?.id ?? GLOBAL_SELECTION)
                        : GLOBAL_SELECTION,
                    )
                  }
                >
                  {form.generationModelId === GLOBAL_SELECTION ? "Pin to Project" : "Use Global"}
                </button>
              )}
            </div>
          ) : (
            <select
              aria-label="Generation model"
              value={form.generationModelId}
              onChange={(event) => changeGenerationModel(event.target.value)}
            >
              <option value={GLOBAL_SELECTION}>Global — {globalModelLabel}</option>
              {allowedGenerationModels.map((model) => (
                <option key={model.id} value={model.id}>
                  {model.id} — {model.provider}/{model.model}
                </option>
              ))}
            </select>
          )}
        </SettingsField>
        <SettingsField
          className={inheritedClass(overrides.responseMode)}
          label="Response mode"
          hint={PROJECT_AI_FIELD_HINTS.responseMode}
        >
          <select
            aria-label="Response mode"
            value={form.responseMode}
            onChange={(event) => changeResponseMode(event.target.value as ResponseModeChoice)}
          >
            <option value="inherit">Global — {RESPONSE_MODE_LABELS[globalResponseMode]}</option>
            <option value="indexed_only">Indexed only</option>
            <option value="indexed_then_web">Indexed, then web</option>
            <option value="indexed_and_web">Indexed and web</option>
          </select>
        </SettingsField>
        <SettingsField
          className={inheritedClass(overrides.groundingAssurance)}
          label="Grounding assurance"
          hint={PROJECT_AI_FIELD_HINTS.grounding}
        >
          <select
            aria-label="Grounding assurance"
            value={form.groundingAssurance}
            onChange={(event) => changeGrounding(event.target.value as GroundingAssurance)}
          >
            <option value="inherit">Global — {GROUNDING_LABELS[globalGrounding]}</option>
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
            <option value="inherit">Global — {globalTranslationEnabled ? "On" : "Off"}</option>
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
              onChange={toggleDomain}
            />
          }
        >
          <textarea
            aria-label="Domain instructions"
            rows={4}
            value={form.domain}
            onChange={(event) => {
              setForm((current) => ({ ...current, domain: event.target.value }));
              setOverride("domain", true);
            }}
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
              disabled={!effective}
              onChange={(event) => changeRerankMode(event.target.value as RerankModeChoice)}
            >
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
              required={customProfile}
              disabled={!effective}
              onChange={(event) => changeTopK(event.target.value)}
            />
          </SettingsField>
        </div>
      </details>
    </>
  );
}
