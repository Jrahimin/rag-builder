/* eslint-disable react-refresh/only-export-components -- helpers shared by Project forms */
import { CircleHelp } from "lucide-react";
import { useState, type Dispatch, type SetStateAction } from "react";
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
  "reason" | "translation" | "rerankMode"
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
  profileId: "",
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
    profileId: stored.execution?.profile_id ?? "",
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
    profileId: hasValue(stored.execution, "profile_id"),
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
  const execution = { ...(stored.execution ?? {}) } as Record<string, unknown>;
  setSparseValue(execution, "profile_id", overrides.profileId, form.profileId);
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
  if (form.rerankMode === "inherit") delete execution.rerank_mode;
  else execution.rerank_mode = form.rerankMode;
  setSparseValue(execution, "retrieval_top_k", overrides.topK, Number(form.topK));
  return { behavior, execution } as ProjectAIConfig;
}

export function sparseHasOverrides(configuration: ProjectAIConfig): boolean {
  const behavior = { ...(configuration.behavior ?? {}) } as Record<string, unknown>;
  const execution = configuration.execution ?? {};
  delete behavior.translation_policy;
  return Object.keys(behavior).length > 0 || Object.keys(execution).length > 0;
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
    "Choose a certified immutable RAG execution profile. Candidate profiles remain Test Lab-only.",
  reason: "A concise audit reason for this immutable revision.",
};

export function ProjectAISettingsFields({
  form,
  setForm,
  overrides,
  setOverride,
  effective,
  defaults,
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
  const baseline = effective
    ? configFormFromEffective(effective)
    : (defaults ?? emptyProjectConfigForm);
  const changeField = <K extends ProjectConfigOverride>(key: K, value: ProjectConfigForm[K]) => {
    setForm((current) => ({ ...current, [key]: value }));
    setOverride(key, value !== baseline[key]);
  };
  const toggleField = (key: ProjectConfigOverride, overridden: boolean) => {
    setOverride(key, overridden);
    if (!overridden) setForm((current) => ({ ...current, [key]: baseline[key] }));
  };
  const inheritedClass = (overridden: boolean) =>
    overridden ? "field-control" : "field-control field-control--inherited";
  return (
    <>
      <div className="form-grid">
        <div className={inheritedClass(overrides.profileId)}>
          <FieldHint label="RAG profile" text={PROJECT_AI_FIELD_HINTS.profile} />
          <select
            aria-label="RAG profile"
            value={form.profileId}
            onChange={(event) => changeField("profileId", event.target.value)}
          >
            <option value="">Custom / deployment baseline</option>
            {ragProfiles.map((profile) => (
              <option key={profile.id} value={profile.id} disabled={!profile.selectable}>
                {profile.id} — {profile.certification_status}
              </option>
            ))}
          </select>
          <InheritanceToggle
            field="RAG profile"
            overridden={overrides.profileId}
            onChange={(enabled) => toggleField("profileId", enabled)}
          />
        </div>
        <div className={inheritedClass(overrides.generationModelId)}>
          <FieldHint label="Generation model" text={PROJECT_AI_FIELD_HINTS.generationModel} />
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
          <InheritanceToggle
            field="Generation model"
            overridden={overrides.generationModelId}
            onChange={(enabled) => toggleField("generationModelId", enabled)}
          />
        </div>
        <div className={inheritedClass(overrides.responseMode)}>
          <FieldHint label="Response mode" text={PROJECT_AI_FIELD_HINTS.responseMode} />
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
          <InheritanceToggle
            field="Response mode"
            overridden={overrides.responseMode}
            onChange={(enabled) => toggleField("responseMode", enabled)}
          />
        </div>
        <div className={inheritedClass(overrides.groundingAssurance)}>
          <FieldHint label="Grounding assurance" text={PROJECT_AI_FIELD_HINTS.grounding} />
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
          <InheritanceToggle
            field="Grounding assurance"
            overridden={overrides.groundingAssurance}
            onChange={(enabled) => toggleField("groundingAssurance", enabled)}
          />
        </div>
        <div className={inheritedClass(overrides.domain)}>
          <FieldHint label="Domain instructions" text={PROJECT_AI_FIELD_HINTS.domain} />
          <textarea
            aria-label="Domain instructions"
            rows={3}
            value={form.domain}
            onChange={(event) => changeField("domain", event.target.value)}
          />
          <InheritanceToggle
            field="Domain instructions"
            overridden={overrides.domain}
            onChange={(enabled) => toggleField("domain", enabled)}
          />
        </div>
      </div>
      <details className="config-advanced">
        <summary>Advanced sparse overrides</summary>
        <div className="form-grid">
          <div className="field-control">
            <FieldHint label="Query translation" text={PROJECT_AI_FIELD_HINTS.translation} />
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
          </div>
          <div className="field-control">
            <FieldHint label="Rerank mode" text={PROJECT_AI_FIELD_HINTS.rerank} />
            <select
              aria-label="Rerank mode"
              value={form.rerankMode}
              onChange={(event) =>
                setForm((current) => ({
                  ...current,
                  rerankMode: event.target.value as RerankModeChoice,
                }))
              }
            >
              <option value="inherit">Inherit deployment default</option>
              <option value="always">Always</option>
              <option value="cross_language">Cross-language</option>
            </select>
          </div>
          <div className={inheritedClass(overrides.topK)}>
            <FieldHint label="Top K" text={PROJECT_AI_FIELD_HINTS.topK} />
            <input
              aria-label="Top K"
              type="number"
              min="1"
              max="100"
              value={form.topK}
              onChange={(event) => changeField("topK", event.target.value)}
            />
            <InheritanceToggle
              field="Top K"
              overridden={overrides.topK}
              onChange={(enabled) => toggleField("topK", enabled)}
            />
          </div>
        </div>
      </details>
    </>
  );
}
