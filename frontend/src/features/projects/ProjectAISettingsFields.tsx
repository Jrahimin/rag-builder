/* eslint-disable react-refresh/only-export-components -- form helpers are shared by Project flows */
import { Check, CircleHelp } from "lucide-react";
import { useState, type Dispatch, type ReactNode, type SetStateAction } from "react";
import type {
  ActiveConfiguration,
  EffectiveProjectAIConfig,
  GenerationModelOption,
  ProjectAIConfig,
  RAGProfileOption,
} from "../../api/operatorApiClient";

export type SettingSource = "global" | "project";
export type SourcedValue<T> = { source: SettingSource; value: T };
export type TranslationMode = "enabled" | "disabled";
export type ResponseModeChoice = "indexed_only" | "indexed_then_web" | "indexed_and_web";
export type GroundingAssurance = "strict" | "balanced";

type ProjectBehaviorForm = {
  generationModelId: SourcedValue<string>;
  responseMode: SourcedValue<ResponseModeChoice>;
  groundingAssurance: SourcedValue<GroundingAssurance>;
  translation: SourcedValue<TranslationMode>;
  domain: SourcedValue<string>;
};

export type ProjectConfigForm = {
  profileId: string;
  customBaseProfileId: string | null;
  execution: Record<string, unknown>;
  behavior: ProjectBehaviorForm;
  reason: string;
};

export const emptyProjectConfigForm: ProjectConfigForm = {
  profileId: "inherit",
  customBaseProfileId: null,
  execution: {},
  behavior: {
    generationModelId: { source: "global", value: "" },
    responseMode: { source: "global", value: "indexed_only" },
    groundingAssurance: { source: "global", value: "strict" },
    translation: { source: "global", value: "disabled" },
    domain: { source: "global", value: "" },
  },
  reason: "",
};

function hasValue(value: object | undefined, key: string) {
  return value !== undefined && Object.prototype.hasOwnProperty.call(value, key);
}

function sourceFor(value: object | undefined, key: string): SettingSource {
  return hasValue(value, key) ? "project" : "global";
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

function effectiveExecution(effective?: EffectiveProjectAIConfig | null) {
  return effective ? materializeExecutionConfiguration(effective.configuration) : {};
}

function deploymentExecution(effective?: EffectiveProjectAIConfig | null) {
  return effective?.deployment_configuration
    ? materializeExecutionConfiguration(effective.deployment_configuration)
    : effectiveExecution(effective);
}

function explicitProfileId(stored: ProjectAIConfig) {
  const execution = (stored.execution ?? {}) as Record<string, unknown>;
  const hasExecutionValues = Object.keys(execution).some((key) => key !== "profile_id");
  return stored.execution?.profile_id ?? (hasExecutionValues ? "custom" : "inherit");
}

export function configFormFromEffective(
  config: EffectiveProjectAIConfig,
  stored: ProjectAIConfig = {},
): ProjectConfigForm {
  const global = config.deployment_configuration ?? config.configuration;
  const behavior = stored.behavior;
  const profileId = explicitProfileId(stored);
  const profile = config.rag_profiles?.find((item) => item.id === profileId);
  const storedExecution = Object.fromEntries(
    Object.entries(stored.execution ?? {}).filter(([key]) => key !== "profile_id"),
  );
  const resolvedExecution = effectiveExecution(config);
  const execution =
    profileId === "inherit"
      ? deploymentExecution(config)
      : profileId === "custom"
        ? { ...resolvedExecution, ...storedExecution }
        : { ...resolvedExecution, ...(profile?.values ?? {}) };
  const customBaseProfileId =
    profileId === "custom" &&
    config.base_profile_id &&
    config.rag_profiles?.some((item) => item.id === config.base_profile_id)
      ? config.base_profile_id
      : null;

  return {
    profileId,
    customBaseProfileId,
    execution,
    behavior: {
      generationModelId: {
        source: sourceFor(behavior, "generation_model_id"),
        value:
          behavior?.generation_model_id ??
          global.llm.generation_model_id ??
          config.configuration.llm.generation_model_id ??
          "",
      },
      responseMode: {
        source: sourceFor(behavior, "response_mode"),
        value: behavior?.response_mode ?? global.chat.response_mode,
      },
      groundingAssurance: {
        source: sourceFor(behavior, "grounding_assurance"),
        value:
          behavior?.grounding_assurance ??
          (global.chat.grounding_mode === "balanced" ? "balanced" : "strict"),
      },
      translation: {
        source:
          behavior?.translation_policy && behavior.translation_policy !== "inherit"
            ? "project"
            : "global",
        value:
          behavior?.translation_policy && behavior.translation_policy !== "inherit"
            ? behavior.translation_policy
            : global.retrieval.query_translation_enabled
              ? "enabled"
              : "disabled",
      },
      domain: {
        source: sourceFor(behavior, "domain_instructions"),
        value: behavior?.domain_instructions ?? global.domain_instructions ?? "",
      },
    },
    reason: "",
  };
}

export function configFormFromDeployment(config: ActiveConfiguration): ProjectConfigForm {
  return {
    ...emptyProjectConfigForm,
    behavior: {
      ...emptyProjectConfigForm.behavior,
      generationModelId: { source: "global", value: config.llm.model ?? "" },
      responseMode: {
        source: "global",
        value: config.chat_response_mode as ResponseModeChoice,
      },
    },
  };
}

export function inheritedFormFromEffective(
  effective?: EffectiveProjectAIConfig | null,
): ProjectConfigForm {
  return effective ? configFormFromEffective(effective) : emptyProjectConfigForm;
}

export function buildSparseProjectConfig(form: ProjectConfigForm): ProjectAIConfig {
  const behavior: Record<string, unknown> = {};
  if (form.behavior.generationModelId.source === "project") {
    behavior.generation_model_id = form.behavior.generationModelId.value;
  }
  if (form.behavior.responseMode.source === "project") {
    behavior.response_mode = form.behavior.responseMode.value;
  }
  if (form.behavior.groundingAssurance.source === "project") {
    behavior.grounding_assurance = form.behavior.groundingAssurance.value;
  }
  if (form.behavior.translation.source === "project") {
    behavior.translation_policy = form.behavior.translation.value;
  }
  if (form.behavior.domain.source === "project") {
    behavior.domain_instructions = form.behavior.domain.value;
  }

  const execution: Record<string, unknown> = {};
  if (form.profileId !== "inherit") execution.profile_id = form.profileId;
  if (form.profileId === "custom") Object.assign(execution, form.execution);
  return { behavior, execution } as ProjectAIConfig;
}

export function sparseHasOverrides(configuration: ProjectAIConfig): boolean {
  return (
    Object.keys(configuration.behavior ?? {}).length > 0 ||
    Object.keys(configuration.execution ?? {}).length > 0
  );
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

const PROFILE_INTENTS: Record<string, string> = {
  economy: "Lower cost and latency",
  standard: "Balanced everyday retrieval",
  quality: "Deeper retrieval for best quality",
};

const RESPONSE_MODE_LABELS: Record<ResponseModeChoice, string> = {
  indexed_only: "Indexed only",
  indexed_then_web: "Indexed, then web",
  indexed_and_web: "Indexed and web",
};

function displayName(value: string) {
  return value.charAt(0).toUpperCase() + value.slice(1).replaceAll("_", " ");
}

function executionSummary(values: Record<string, unknown>): string {
  const semantic = values.semantic_candidate_top_k;
  const keyword = values.keyword_candidate_top_k;
  const summary = [
    typeof semantic === "number" || typeof keyword === "number"
      ? `Candidates ${typeof semantic === "number" ? semantic : "—"}/${typeof keyword === "number" ? keyword : "—"}`
      : null,
    typeof values.rerank_mode === "string" ? `Rerank ${displayName(values.rerank_mode)}` : null,
    typeof values.retrieval_top_k === "number" ? `Top K ${values.retrieval_top_k}` : null,
    typeof values.max_context_chunks === "number" ? `Context ${values.max_context_chunks}` : null,
  ].filter(Boolean);
  return summary.join(" · ") || "Uses the current execution bundle";
}

type ExecutionFieldDefinition = {
  key: string;
  label: string;
  kind?: "number" | "boolean" | "rerank";
  min?: number;
  max?: number;
  step?: number;
  nullable?: boolean;
};

const CORE_EXECUTION_FIELDS: ExecutionFieldDefinition[] = [
  { key: "semantic_candidate_top_k", label: "Semantic candidates", min: 1, max: 200 },
  { key: "keyword_candidate_top_k", label: "Keyword candidates", min: 1, max: 200 },
  { key: "rerank_mode", label: "Rerank mode", kind: "rerank" },
  { key: "rerank_candidate_window", label: "Rerank window", min: 1, max: 100 },
  { key: "retrieval_top_k", label: "Top K", min: 1, max: 100 },
  { key: "max_context_chunks", label: "Context chunks", min: 1, max: 50 },
  { key: "context_char_budget", label: "Context budget", min: 500, max: 200_000 },
  { key: "max_history_messages", label: "History messages", min: 0, max: 200 },
];

const ADVANCED_EXECUTION_FIELDS: ExecutionFieldDefinition[] = [
  { key: "hnsw_ef_search", label: "HNSW search depth", min: 1, max: 1000 },
  { key: "rrf_k", label: "RRF K", min: 1, max: 500 },
  { key: "semantic_weight", label: "Semantic weight", min: 0, max: 10, step: 0.1 },
  { key: "keyword_weight", label: "Keyword weight", min: 0, max: 10, step: 0.1 },
  {
    key: "score_threshold",
    label: "Score threshold",
    min: 0,
    max: 1,
    step: 0.01,
    nullable: true,
  },
  {
    key: "rerank_score_threshold",
    label: "Rerank threshold",
    min: 0,
    max: 1,
    step: 0.01,
    nullable: true,
  },
  {
    key: "min_ocr_confidence",
    label: "Minimum OCR confidence",
    min: 0,
    max: 1,
    step: 0.01,
    nullable: true,
  },
  { key: "max_chunks_per_document", label: "Chunks per document", min: 1, max: 100 },
  { key: "max_chunks_per_section", label: "Chunks per section", min: 1, max: 100 },
  { key: "deduplicate_by_content_hash", label: "Content deduplication", kind: "boolean" },
  { key: "passage_scoring_enabled", label: "Passage scoring", kind: "boolean" },
  { key: "passage_window_tokens", label: "Passage window", min: 16, max: 512 },
  { key: "passage_overlap_tokens", label: "Passage overlap", min: 0, max: 256 },
  { key: "passage_min_tokens", label: "Minimum passage", min: 8, max: 256 },
  { key: "max_related_sources", label: "Related sources", min: 1, max: 8 },
  { key: "max_relationship_candidates", label: "Relationship candidates", min: 1, max: 20 },
];

function ExecutionField({
  definition,
  value,
  onChange,
}: {
  definition: ExecutionFieldDefinition;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  return (
    <label className="execution-field">
      <span>{definition.label}</span>
      {definition.kind === "rerank" ? (
        <select
          aria-label={definition.label}
          value={typeof value === "string" ? value : "always"}
          onChange={(event) => onChange(event.target.value)}
        >
          <option value="always">Always</option>
          <option value="cross_language">Cross-language</option>
        </select>
      ) : definition.kind === "boolean" ? (
        <select
          aria-label={definition.label}
          value={value === false ? "false" : "true"}
          onChange={(event) => onChange(event.target.value === "true")}
        >
          <option value="true">On</option>
          <option value="false">Off</option>
        </select>
      ) : (
        <input
          aria-label={definition.label}
          type="number"
          min={definition.min}
          max={definition.max}
          step={definition.step ?? 1}
          value={typeof value === "number" ? value : ""}
          required={!definition.nullable}
          onChange={(event) =>
            onChange(event.target.value === "" ? null : Number(event.target.value))
          }
        />
      )}
    </label>
  );
}

function BehaviorSetting({
  label,
  hint,
  source,
  globalDisplay,
  canOverride = true,
  onOverride,
  onUseGlobal,
  children,
}: {
  label: string;
  hint: string;
  source: SettingSource;
  globalDisplay: ReactNode;
  canOverride?: boolean;
  onOverride: () => void;
  onUseGlobal: () => void;
  children: ReactNode;
}) {
  return (
    <div className={`behavior-setting behavior-setting--${source}`}>
      <div className="behavior-setting__header">
        <FieldHint label={label} text={hint} />
        <div className="behavior-setting__source">
          <span className={`source-badge source-badge--${source}`}>
            {source === "global" ? "Global" : "Project"}
          </span>
          <button
            type="button"
            className="source-action"
            aria-label={`${label}: ${source === "global" ? "Override" : "Use Global"}`}
            disabled={source === "global" && !canOverride}
            onClick={source === "global" ? onOverride : onUseGlobal}
          >
            {source === "global" ? "Override" : "Use Global"}
          </button>
        </div>
      </div>
      {source === "global" ? (
        <div className="behavior-setting__effective">{globalDisplay}</div>
      ) : (
        children
      )}
    </div>
  );
}

const BEHAVIOR_HINTS = {
  generationModel: "Pin a deployment-approved logical generation model for this Project.",
  responseMode: "Choose indexed-only, fallback web, or indexed-and-web evidence behavior.",
  grounding: "Choose the Project's bounded grounding posture.",
  translation: "Enable translation only for cross-language retrieval.",
  domain: "Project-specific standing instructions. This is not a full system prompt.",
};

function executionMatches(left: Record<string, unknown>, right: Record<string, unknown>) {
  const keys = Object.keys(right);
  return keys.length > 0 && keys.every((key) => left[key] === right[key]);
}

export function ProjectAISettingsFields({
  form,
  setForm,
  effective,
  allowedGenerationModels = [],
  ragProfiles = [],
  globalRagProfileId,
}: {
  form: ProjectConfigForm;
  setForm: Dispatch<SetStateAction<ProjectConfigForm>>;
  effective?: EffectiveProjectAIConfig | null;
  allowedGenerationModels?: GenerationModelOption[];
  ragProfiles?: RAGProfileOption[];
  globalRagProfileId?: string | null;
}) {
  const globalProfileId =
    globalRagProfileId ??
    effective?.provenance.deployment_default_execution_profile_id ??
    "standard";
  const globalExecution = deploymentExecution(effective);
  const customProfile = form.profileId === "custom";
  const matchingProfile = customProfile
    ? ragProfiles.find((profile) => executionMatches(form.execution, profile.values))
    : undefined;

  const selectProfile = (profileId: string) => {
    const profile = ragProfiles.find((item) => item.id === profileId);
    setForm((current) => {
      const nextExecution =
        profileId === "inherit"
          ? globalExecution
          : profileId === "custom"
            ? current.execution
            : { ...effectiveExecution(effective), ...(profile?.values ?? {}) };
      return {
        ...current,
        profileId,
        customBaseProfileId:
          profileId === "custom"
            ? current.profileId === "inherit"
              ? globalProfileId
              : current.profileId === "custom"
                ? current.customBaseProfileId
                : current.profileId
            : null,
        execution: { ...nextExecution },
      };
    });
  };

  const changeExecution = (key: string, value: unknown) => {
    setForm((current) => ({
      ...current,
      profileId: "custom",
      customBaseProfileId:
        current.profileId === "custom"
          ? current.customBaseProfileId
          : current.profileId === "inherit"
            ? globalProfileId
            : current.profileId,
      execution: { ...current.execution, [key]: value },
    }));
  };

  const setBehaviorSource = <K extends keyof ProjectBehaviorForm>(
    key: K,
    source: SettingSource,
    globalValue?: ProjectBehaviorForm[K]["value"],
  ) => {
    setForm((current) => ({
      ...current,
      behavior: {
        ...current.behavior,
        [key]: {
          source,
          value:
            source === "global" && globalValue !== undefined
              ? globalValue
              : current.behavior[key].value,
        },
      },
    }));
  };

  const changeBehavior = <K extends keyof ProjectBehaviorForm>(
    key: K,
    value: ProjectBehaviorForm[K]["value"],
  ) => {
    setForm((current) => ({
      ...current,
      behavior: { ...current.behavior, [key]: { source: "project", value } },
    }));
  };

  const globalConfig = effective?.deployment_configuration ?? effective?.configuration;
  const globalModelId =
    globalConfig?.llm.generation_model_id ?? form.behavior.generationModelId.value;
  const globalModel = allowedGenerationModels.find((model) => model.id === globalModelId);
  const globalResponse = globalConfig?.chat.response_mode ?? form.behavior.responseMode.value;
  const globalGrounding: GroundingAssurance =
    globalConfig?.chat.grounding_mode === "balanced" ? "balanced" : "strict";
  const globalTranslation: TranslationMode = globalConfig?.retrieval.query_translation_enabled
    ? "enabled"
    : "disabled";
  const globalDomain = globalConfig?.domain_instructions ?? "";
  const oneModel = allowedGenerationModels.length <= 1;
  const canOverrideModel = allowedGenerationModels.length > 0;

  return (
    <div className="ai-config-sections">
      <section className="ai-config-section" aria-labelledby="rag-execution-heading">
        <header className="ai-config-section__header">
          <div>
            <p className="eyebrow">RAG execution</p>
            <h3 id="rag-execution-heading">Retrieval and context template</h3>
          </div>
          <span className="section-state">
            {form.profileId === "inherit"
              ? `Global · currently ${displayName(globalProfileId)}`
              : customProfile
                ? `Custom${form.customBaseProfileId ? ` · based on ${displayName(form.customBaseProfileId)}` : ""}`
                : displayName(form.profileId)}
          </span>
        </header>

        <div className="profile-selector__cards" role="radiogroup" aria-label="RAG profile">
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
            <span className="profile-card__title">
              Global
              <small>Currently {displayName(globalProfileId)}</small>
              {form.profileId === "inherit" && <Check size={15} aria-hidden="true" />}
            </span>
            <span className="profile-card__intent">Follows deployment changes</span>
            <span className="profile-card__summary">{executionSummary(globalExecution)}</span>
          </button>
          {ragProfiles.map((profile) => (
            <button
              key={profile.id}
              type="button"
              role="radio"
              aria-checked={form.profileId === profile.id}
              aria-label={`${displayName(profile.id)} RAG profile`}
              disabled={!profile.selectable && form.profileId !== profile.id}
              className={
                form.profileId === profile.id
                  ? "profile-card profile-card--selected"
                  : "profile-card"
              }
              onClick={() => selectProfile(profile.id)}
            >
              <span className="profile-card__title">
                {displayName(profile.id)}
                {profile.recommended && <small>Recommended</small>}
                {form.profileId === profile.id && <Check size={15} aria-hidden="true" />}
              </span>
              <span className="profile-card__intent">
                {PROFILE_INTENTS[profile.id] ?? "Predefined execution template"}
              </span>
              <span className="profile-card__summary">{executionSummary(profile.values)}</span>
            </button>
          ))}
        </div>

        {effective ? (
          <>
            <div
              className={
                customProfile
                  ? "custom-profile-state custom-profile-state--active"
                  : "custom-profile-state"
              }
            >
              <div>
                <strong>
                  {customProfile
                    ? `Custom${form.customBaseProfileId ? ` · based on ${displayName(form.customBaseProfileId)}` : ""}`
                    : "Need different execution values?"}
                </strong>
                <span>
                  {matchingProfile
                    ? `Matches ${displayName(matchingProfile.id)}; remains explicitly Custom.`
                    : customProfile
                      ? "Every execution value is explicit in the revision."
                      : "Editing any setting below creates a complete Custom bundle."}
                </span>
              </div>
              {!customProfile && (
                <button
                  type="button"
                  className="button button--secondary"
                  onClick={() => selectProfile("custom")}
                >
                  Customize current
                </button>
              )}
            </div>

            <div className="execution-settings">
              <div className="execution-settings__heading">
                <h4>Core execution settings</h4>
                <span>{customProfile ? "Editable Custom bundle" : "Edit to create Custom"}</span>
              </div>
              <div className="execution-grid execution-grid--core">
                {CORE_EXECUTION_FIELDS.map((definition) => (
                  <ExecutionField
                    key={definition.key}
                    definition={definition}
                    value={form.execution[definition.key]}
                    onChange={(value) => changeExecution(definition.key, value)}
                  />
                ))}
              </div>
              <details className="config-advanced execution-advanced">
                <summary>Advanced execution settings</summary>
                <div className="execution-grid">
                  {ADVANCED_EXECUTION_FIELDS.map((definition) => (
                    <ExecutionField
                      key={definition.key}
                      definition={definition}
                      value={form.execution[definition.key]}
                      onChange={(value) => changeExecution(definition.key, value)}
                    />
                  ))}
                </div>
              </details>
            </div>
          </>
        ) : (
          <p className="muted-copy">
            Execution templates can be customized after the Project is created.
          </p>
        )}
      </section>

      <section className="ai-config-section" aria-labelledby="project-behavior-heading">
        <header className="ai-config-section__header">
          <div>
            <p className="eyebrow">Project behavior</p>
            <h3 id="project-behavior-heading">Answer behavior and instructions</h3>
          </div>
          <span className="section-state">Sparse Project overrides</span>
        </header>
        <div className="behavior-grid">
          <BehaviorSetting
            label="Generation model"
            hint={BEHAVIOR_HINTS.generationModel}
            source={form.behavior.generationModelId.source}
            globalDisplay={
              <span>
                <strong>{globalModelId || "Deployment default"}</strong>
                {globalModel && (
                  <small>
                    {globalModel.provider}/{globalModel.model}
                  </small>
                )}
              </span>
            }
            canOverride={canOverrideModel}
            onOverride={() => {
              const current = form.behavior.generationModelId.value;
              const next = allowedGenerationModels.some((model) => model.id === current)
                ? current
                : (allowedGenerationModels[0]?.id ?? current);
              changeBehavior("generationModelId", next);
            }}
            onUseGlobal={() => setBehaviorSource("generationModelId", "global", globalModelId)}
          >
            {oneModel ? (
              <div className="behavior-setting__effective">
                <strong>{form.behavior.generationModelId.value}</strong>
              </div>
            ) : (
              <select
                aria-label="Generation model"
                value={form.behavior.generationModelId.value}
                onChange={(event) => changeBehavior("generationModelId", event.target.value)}
              >
                {allowedGenerationModels.map((model) => (
                  <option key={model.id} value={model.id}>
                    {model.id} — {model.provider}/{model.model}
                  </option>
                ))}
              </select>
            )}
          </BehaviorSetting>

          <BehaviorSetting
            label="Response mode"
            hint={BEHAVIOR_HINTS.responseMode}
            source={form.behavior.responseMode.source}
            globalDisplay={<strong>{RESPONSE_MODE_LABELS[globalResponse]}</strong>}
            onOverride={() => setBehaviorSource("responseMode", "project")}
            onUseGlobal={() => setBehaviorSource("responseMode", "global", globalResponse)}
          >
            <select
              aria-label="Response mode"
              value={form.behavior.responseMode.value}
              onChange={(event) =>
                changeBehavior("responseMode", event.target.value as ResponseModeChoice)
              }
            >
              {Object.entries(RESPONSE_MODE_LABELS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </BehaviorSetting>

          <BehaviorSetting
            label="Grounding assurance"
            hint={BEHAVIOR_HINTS.grounding}
            source={form.behavior.groundingAssurance.source}
            globalDisplay={<strong>{displayName(globalGrounding)}</strong>}
            onOverride={() => setBehaviorSource("groundingAssurance", "project")}
            onUseGlobal={() => setBehaviorSource("groundingAssurance", "global", globalGrounding)}
          >
            <select
              aria-label="Grounding assurance"
              value={form.behavior.groundingAssurance.value}
              onChange={(event) =>
                changeBehavior("groundingAssurance", event.target.value as GroundingAssurance)
              }
            >
              <option value="strict">Strict</option>
              <option value="balanced">Balanced</option>
            </select>
          </BehaviorSetting>

          <BehaviorSetting
            label="Query translation"
            hint={BEHAVIOR_HINTS.translation}
            source={form.behavior.translation.source}
            globalDisplay={<strong>{globalTranslation === "enabled" ? "On" : "Off"}</strong>}
            onOverride={() => setBehaviorSource("translation", "project")}
            onUseGlobal={() => setBehaviorSource("translation", "global", globalTranslation)}
          >
            <select
              aria-label="Query translation"
              value={form.behavior.translation.value}
              onChange={(event) =>
                changeBehavior("translation", event.target.value as TranslationMode)
              }
            >
              <option value="disabled">Off</option>
              <option value="enabled">On</option>
            </select>
          </BehaviorSetting>

          <BehaviorSetting
            label="Domain instructions"
            hint={BEHAVIOR_HINTS.domain}
            source={form.behavior.domain.source}
            globalDisplay={
              <span className="domain-preview">{globalDomain || "No Global instructions"}</span>
            }
            onOverride={() => setBehaviorSource("domain", "project")}
            onUseGlobal={() => setBehaviorSource("domain", "global", globalDomain)}
          >
            <textarea
              aria-label="Domain instructions"
              rows={4}
              value={form.behavior.domain.value}
              onChange={(event) => changeBehavior("domain", event.target.value)}
            />
          </BehaviorSetting>
        </div>
      </section>
    </div>
  );
}
