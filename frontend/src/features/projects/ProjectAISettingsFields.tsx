/* eslint-disable react-refresh/only-export-components -- helpers shared by Create Project and AI Configuration */
import { CircleHelp } from "lucide-react";
import { useState, type Dispatch, type SetStateAction } from "react";
import type {
  ActiveConfiguration,
  EffectiveProjectAIConfig,
  ProjectAIConfig,
  ProviderCapability,
} from "../../api/operatorApiClient";

export type TranslationMode = "inherit" | "on" | "off";
export type RerankModeChoice = "inherit" | "always" | "cross_language" | "off";
export type ResponseModeChoice = "indexed_only" | "indexed_then_web" | "indexed_and_web";

export type ProjectConfigForm = {
  provider: string;
  model: string;
  temperature: string;
  maxTokens: string;
  responseMode: ResponseModeChoice;
  webSearchEnabled: boolean;
  webSearchModel: string;
  webSearchMaxResults: string;
  webSearchMaxEvidenceChars: string;
  webSearchMaxOutputTokens: string;
  webSearchTimeout: string;
  domain: string;
  topK: string;
  strategy: string;
  translation: TranslationMode;
  rerankMode: RerankModeChoice;
  evidence: string;
  citations: boolean;
  sourcePolicy: string;
  reason: string;
};

export type ProjectConfigOverride = Exclude<
  keyof ProjectConfigForm,
  "reason" | "translation" | "rerankMode"
>;
export type ProjectConfigOverrides = Record<ProjectConfigOverride, boolean>;

export const inheritedProjectConfig: ProjectConfigOverrides = {
  provider: false,
  model: false,
  temperature: false,
  maxTokens: false,
  responseMode: false,
  webSearchEnabled: false,
  webSearchModel: false,
  webSearchMaxResults: false,
  webSearchMaxEvidenceChars: false,
  webSearchMaxOutputTokens: false,
  webSearchTimeout: false,
  domain: false,
  topK: false,
  strategy: false,
  evidence: false,
  citations: false,
  sourcePolicy: false,
};

export const emptyProjectConfigForm: ProjectConfigForm = {
  provider: "",
  model: "",
  temperature: "",
  maxTokens: "",
  responseMode: "indexed_only",
  webSearchEnabled: false,
  webSearchModel: "",
  webSearchMaxResults: "",
  webSearchMaxEvidenceChars: "",
  webSearchMaxOutputTokens: "",
  webSearchTimeout: "",
  domain: "",
  topK: "",
  strategy: "hybrid",
  translation: "inherit",
  rerankMode: "inherit",
  evidence: "",
  citations: true,
  sourcePolicy: "off",
  reason: "",
};

function hasValue(value: object | undefined, key: string) {
  return value !== undefined && Object.prototype.hasOwnProperty.call(value, key);
}

export function storedTranslationMode(stored: ProjectAIConfig): TranslationMode {
  if (!hasValue(stored.retrieval, "query_translation_enabled")) return "inherit";
  return stored.retrieval?.query_translation_enabled ? "on" : "off";
}

export function storedRerankMode(stored: ProjectAIConfig): RerankModeChoice {
  if (hasValue(stored.retrieval, "rerank_mode") && stored.retrieval?.rerank_mode) {
    return stored.retrieval.rerank_mode;
  }
  if (hasValue(stored.retrieval, "rerank_enabled") && stored.retrieval?.rerank_enabled != null) {
    return stored.retrieval.rerank_enabled ? "always" : "off";
  }
  return "inherit";
}

export function configFormFromEffective(
  config: EffectiveProjectAIConfig,
  stored: ProjectAIConfig = {},
  deploymentConfiguration?: EffectiveProjectAIConfig["configuration"] | null,
): ProjectConfigForm {
  const configuration = config.configuration;
  const inheritsTranslation =
    deploymentConfiguration != null &&
    configuration.retrieval.query_translation_enabled ===
      deploymentConfiguration.retrieval.query_translation_enabled;
  const inheritsRerank =
    deploymentConfiguration != null &&
    configuration.retrieval.rerank_mode === deploymentConfiguration.retrieval.rerank_mode;
  return {
    provider: configuration.llm.provider,
    model: configuration.llm.model,
    temperature:
      configuration.llm.temperature === null
        ? ""
        : String(configuration.llm.temperature),
    maxTokens: String(configuration.llm.max_tokens),
    responseMode: configuration.chat.response_mode,
    webSearchEnabled: configuration.web_search.enabled,
    webSearchModel: configuration.web_search.model,
    webSearchMaxResults: String(configuration.web_search.max_results),
    webSearchMaxEvidenceChars: String(configuration.web_search.max_evidence_chars),
    webSearchMaxOutputTokens: String(configuration.web_search.max_output_tokens),
    webSearchTimeout: String(configuration.web_search.request_timeout_seconds),
    domain: configuration.domain_instructions,
    topK: String(configuration.retrieval.top_k),
    strategy: configuration.retrieval.strategy,
    translation: inheritsTranslation ? "inherit" : storedTranslationMode(stored),
    rerankMode: inheritsRerank ? "inherit" : storedRerankMode(stored),
    evidence: String(configuration.retrieval.semantic_evidence_score_threshold),
    citations: configuration.chat.include_citations,
    sourcePolicy: configuration.source_policy_mode,
    reason: "",
  };
}

export function configOverridesFromEffective(
  config: EffectiveProjectAIConfig,
  deploymentConfiguration?: EffectiveProjectAIConfig["configuration"] | null,
): ProjectConfigOverrides {
  if (!deploymentConfiguration) return configOverridesFromStored(config.configuration);
  const form = configFormFromEffective(config);
  const baseline = configFormFromEffective({
    ...config,
    configuration: deploymentConfiguration,
  });
  return {
    provider: form.provider !== baseline.provider,
    model: form.model !== baseline.model,
    temperature: form.temperature !== baseline.temperature,
    maxTokens: form.maxTokens !== baseline.maxTokens,
    responseMode: form.responseMode !== baseline.responseMode,
    webSearchEnabled: form.webSearchEnabled !== baseline.webSearchEnabled,
    webSearchModel: form.webSearchModel !== baseline.webSearchModel,
    webSearchMaxResults: form.webSearchMaxResults !== baseline.webSearchMaxResults,
    webSearchMaxEvidenceChars:
      form.webSearchMaxEvidenceChars !== baseline.webSearchMaxEvidenceChars,
    webSearchMaxOutputTokens:
      form.webSearchMaxOutputTokens !== baseline.webSearchMaxOutputTokens,
    webSearchTimeout: form.webSearchTimeout !== baseline.webSearchTimeout,
    domain: form.domain !== baseline.domain,
    topK: form.topK !== baseline.topK,
    strategy: form.strategy !== baseline.strategy,
    evidence: form.evidence !== baseline.evidence,
    citations: form.citations !== baseline.citations,
    sourcePolicy: form.sourcePolicy !== baseline.sourcePolicy,
  };
}

export function configOverridesFromStored(stored: ProjectAIConfig): ProjectConfigOverrides {
  return {
    provider: hasValue(stored.llm, "provider"),
    model: hasValue(stored.llm, "model"),
    temperature: hasValue(stored.llm, "temperature") && stored.llm?.temperature !== null,
    maxTokens: hasValue(stored.llm, "max_tokens"),
    responseMode: hasValue(stored.chat, "response_mode"),
    webSearchEnabled: hasValue(stored.web_search, "enabled"),
    webSearchModel: hasValue(stored.web_search, "model"),
    webSearchMaxResults: hasValue(stored.web_search, "max_results"),
    webSearchMaxEvidenceChars: hasValue(stored.web_search, "max_evidence_chars"),
    webSearchMaxOutputTokens: hasValue(stored.web_search, "max_output_tokens"),
    webSearchTimeout: hasValue(stored.web_search, "request_timeout_seconds"),
    domain: hasValue(stored, "domain_instructions"),
    topK: hasValue(stored.retrieval, "top_k"),
    strategy: hasValue(stored.retrieval, "strategy"),
    evidence: hasValue(stored.retrieval, "evidence_score_threshold"),
    citations: hasValue(stored.chat, "include_citations"),
    sourcePolicy: hasValue(stored, "source_policy_mode"),
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
  capability: ProviderCapability | undefined,
): ProjectAIConfig {
  const configuration = { ...stored } as Record<string, unknown>;
  const llm = { ...(stored.llm ?? {}) } as Record<string, unknown>;
  const retrieval = { ...(stored.retrieval ?? {}) } as Record<string, unknown>;
  const chat = { ...(stored.chat ?? {}) } as Record<string, unknown>;
  const webSearch = { ...(stored.web_search ?? {}) } as Record<string, unknown>;

  setSparseValue(llm, "provider", overrides.provider, form.provider);
  setSparseValue(llm, "model", overrides.model, form.model);
  setSparseValue(
    llm,
    "temperature",
    overrides.temperature && capability?.parameters.temperature.supported !== false,
    form.temperature === "" ? null : Number(form.temperature),
  );
  setSparseValue(llm, "max_tokens", overrides.maxTokens, Number(form.maxTokens));
  setSparseValue(chat, "response_mode", overrides.responseMode, form.responseMode);
  setSparseValue(webSearch, "enabled", overrides.webSearchEnabled, form.webSearchEnabled);
  setSparseValue(webSearch, "model", overrides.webSearchModel, form.webSearchModel);
  setSparseValue(
    webSearch,
    "max_results",
    overrides.webSearchMaxResults,
    Number(form.webSearchMaxResults),
  );
  setSparseValue(
    webSearch,
    "max_evidence_chars",
    overrides.webSearchMaxEvidenceChars,
    Number(form.webSearchMaxEvidenceChars),
  );
  setSparseValue(
    webSearch,
    "max_output_tokens",
    overrides.webSearchMaxOutputTokens,
    Number(form.webSearchMaxOutputTokens),
  );
  setSparseValue(
    webSearch,
    "request_timeout_seconds",
    overrides.webSearchTimeout,
    Number(form.webSearchTimeout),
  );
  setSparseValue(retrieval, "top_k", overrides.topK, Number(form.topK));
  setSparseValue(retrieval, "strategy", overrides.strategy, form.strategy);
  if (form.translation === "inherit") {
    delete retrieval.query_translation_enabled;
  } else {
    retrieval.query_translation_enabled = form.translation === "on";
  }
  if (form.rerankMode === "inherit") {
    delete retrieval.rerank_mode;
    delete retrieval.rerank_enabled;
  } else {
    retrieval.rerank_mode = form.rerankMode;
    delete retrieval.rerank_enabled;
  }
  setSparseValue(retrieval, "evidence_score_threshold", overrides.evidence, Number(form.evidence));
  setSparseValue(chat, "include_citations", overrides.citations, form.citations);
  setSparseValue(configuration, "domain_instructions", overrides.domain, form.domain);
  setSparseValue(configuration, "source_policy_mode", overrides.sourcePolicy, form.sourcePolicy);

  configuration.llm = llm;
  configuration.retrieval = retrieval;
  configuration.chat = chat;
  configuration.web_search = webSearch;
  return configuration;
}

export function sparseHasOverrides(configuration: ProjectAIConfig): boolean {
  const llm = configuration.llm ?? {};
  const retrieval = configuration.retrieval ?? {};
  const chat = configuration.chat ?? {};
  const webSearch = configuration.web_search ?? {};
  return (
    Object.keys(llm).length > 0 ||
    Object.keys(retrieval).length > 0 ||
    Object.keys(chat).length > 0 ||
    Object.keys(webSearch).length > 0 ||
    configuration.domain_instructions != null ||
    configuration.prompt_profile != null ||
    configuration.prompt_version != null ||
    configuration.source_policy_mode != null
  );
}

export function inheritedFormFromEffective(
  effective?: EffectiveProjectAIConfig | null,
): ProjectConfigForm {
  if (!effective) return { ...emptyProjectConfigForm };
  return configFormFromEffective({
    ...effective,
    configuration: effective.deployment_configuration ?? effective.configuration,
  });
}

export function configFormFromDeployment(config: ActiveConfiguration): ProjectConfigForm {
  return {
    ...emptyProjectConfigForm,
    provider: config.llm.backend || "",
    model: config.llm.model ?? "",
    responseMode: config.chat_response_mode as ResponseModeChoice,
    strategy: config.retrieval_strategy || "hybrid",
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
  provider:
    "Which AI vendor this Project uses for chat.\n\nLeave Inherit on to keep the deployment vendor. Example: openai in this environment. Pick ollama only when this Project should call a different vendor.",
  model:
    "The exact model name sent to that vendor.\n\nLeave Inherit on to keep the deployment model. Example: gpt-4.1-mini. Type a different name only for a Project-specific model.",
  responseMode:
    "Controls which evidence chat may use.\n\nIndexed only stays inside the Project knowledge base. Indexed then web searches the web only when Project evidence is insufficient. Indexed and web always retrieves both. Document-scoped questions remain limited to Project knowledge.",
  webSearchEnabled:
    "Allows this Project to use the deployment web-search provider when its response mode needs web evidence. Turn it off to keep this Project indexed-only even when web search is globally available.",
  webSearchModel:
    "Model used for the web-search Responses call. Inherit uses the configured web model, or this Project’s chat model when no dedicated web model is configured.",
  webSearchMaxResults:
    "Maximum cited web sources considered for a chat turn. Lower values reduce latency and cost.",
  webSearchMaxEvidenceChars:
    "Maximum total characters admitted from accepted web evidence. This is a hard prompt-budget bound.",
  webSearchMaxOutputTokens:
    "Maximum tokens permitted for the web-search provider response before evidence extraction.",
  webSearchTimeout: "Maximum seconds to wait for the web-search provider before it fails closed.",
  domain:
    "Extra standing rules for this Project, prepended to the platform’s system prompt. This is not the full system prompt.\n\nExample: “Answer in Bengali. Use official NBR tax terms. Never invent a section number.”",
  translation:
    "Translate the user’s question before search when it is in a different language than the documents.\n\nExample: a Bangla question over English PDFs. Inherit follows the deployment on/off setting.",
  rerank:
    "After search, reorder passages so the best evidence sits first.\n\nAlways: every question. Cross-language: only when languages differ. Off: skip rerank and use the search order.",
  citations:
    "Attach short source excerpts to grounded answers so you can check the evidence.\n\nLeave on for operator review. Turn off only if this Project should reply without citations.",
  temperature:
    "How varied the wording can be. Lower is more repeatable; higher is more free-form.\n\nExample: 0.2 for policy answers. Some models ignore this setting entirely.",
  maxTokens:
    "Hard cap on how long a generated answer may be, in tokens.\n\nExample: 2048. Inherit uses the deployment cap.",
  strategy:
    "How passages are found before answering.\n\nSemantic: meaning only. Hybrid: meaning plus keyword match — better for IDs, names, and mixed Bangla/English text.",
  topK: "How many passages to retrieve before answering.\n\nExample: 10. Higher can add context but costs more and may add noise.",
  evidence:
    "Minimum retrieval score before the model may answer from a passage.\n\nExample: 0.35. Below that, the Project should refuse rather than guess.",
  sourcePolicy:
    "Whether retrieval must honor source lifecycle and relationships (replaces / modifies).\n\nOff: all indexed text. Enforce: only applicable current sources.",
  reason:
    "Short note stored with this immutable revision for audit.\n\nExample: “Raise the evidence floor after false citations.”",
};

export function ProjectAISettingsFields({
  form,
  setForm,
  overrides,
  setOverride,
  effective,
  deploymentConfiguration,
  defaults,
}: {
  form: ProjectConfigForm;
  setForm: Dispatch<SetStateAction<ProjectConfigForm>>;
  overrides: ProjectConfigOverrides;
  setOverride: (key: ProjectConfigOverride, enabled: boolean) => void;
  effective?: EffectiveProjectAIConfig | null;
  deploymentConfiguration?: EffectiveProjectAIConfig["configuration"] | null;
  defaults?: ProjectConfigForm;
}) {
  const baseline = effective
    ? configFormFromEffective({
        ...effective,
        configuration: deploymentConfiguration ?? effective.configuration,
      })
    : (defaults ?? emptyProjectConfigForm);
  const baselineConfiguration = deploymentConfiguration ?? effective?.configuration;
  const translationHint: Exclude<TranslationMode, "inherit"> = baselineConfiguration?.retrieval
    .query_translation_enabled
    ? "on"
    : "off";
  const resolvedRerank = baselineConfiguration?.retrieval.rerank_mode;
  const rerankHint: Exclude<RerankModeChoice, "inherit"> =
    resolvedRerank === "cross_language" || resolvedRerank === "off" || resolvedRerank === "always"
      ? resolvedRerank
      : "always";
  const changeField = <K extends ProjectConfigOverride>(key: K, value: ProjectConfigForm[K]) => {
    setForm((current) => ({ ...current, [key]: value }));
    setOverride(key, value !== baseline[key]);
  };
  const toggleField = (key: ProjectConfigOverride, overridden: boolean) => {
    setOverride(key, overridden);
    if (!overridden) {
      setForm((current) => ({ ...current, [key]: baseline[key] }));
    }
  };
  const inheritedClass = (overridden: boolean) =>
    overridden ? "field-control" : "field-control field-control--inherited";
  return (
    <div className="form-grid">
      <div className={inheritedClass(overrides.provider)}>
        <FieldHint label="Provider" text={PROJECT_AI_FIELD_HINTS.provider} />
        <select
          aria-label="Provider"
          value={form.provider}
          onChange={(event) => changeField("provider", event.target.value)}
        >
          {!baseline.provider && <option value="">Deployment default</option>}
          {["echo", "openai", "openai_compatible", "ollama", "gemini"].map((value) => (
            <option key={value}>{value}</option>
          ))}
        </select>
        <InheritanceToggle
          field="Provider"
          overridden={overrides.provider}
          onChange={(enabled) => toggleField("provider", enabled)}
        />
      </div>
      <div className={inheritedClass(overrides.model)}>
        <FieldHint label="Model" text={PROJECT_AI_FIELD_HINTS.model} />
        <input
          aria-label="Model"
          value={form.model}
          placeholder={overrides.model ? undefined : "Deployment default"}
          onChange={(event) => changeField("model", event.target.value)}
        />
        <InheritanceToggle
          field="Model"
          overridden={overrides.model}
          onChange={(enabled) => toggleField("model", enabled)}
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
          <option value="indexed_then_web">Indexed, then web fallback</option>
          <option value="indexed_and_web">Indexed and web</option>
        </select>
        <InheritanceToggle
          field="Response mode"
          overridden={overrides.responseMode}
          onChange={(enabled) => toggleField("responseMode", enabled)}
        />
      </div>
      <div className={inheritedClass(overrides.webSearchEnabled)}>
        <FieldHint label="Web search" text={PROJECT_AI_FIELD_HINTS.webSearchEnabled} />
        <label className="check-control">
          <input
            type="checkbox"
            checked={form.webSearchEnabled}
            onChange={(event) => changeField("webSearchEnabled", event.target.checked)}
          />{" "}
          Allow web evidence
        </label>
        <InheritanceToggle
          field="Web search"
          overridden={overrides.webSearchEnabled}
          onChange={(enabled) => toggleField("webSearchEnabled", enabled)}
        />
      </div>
      <div className={inheritedClass(overrides.webSearchModel)}>
        <FieldHint label="Web search model" text={PROJECT_AI_FIELD_HINTS.webSearchModel} />
        <input
          aria-label="Web search model"
          value={form.webSearchModel}
          placeholder={overrides.webSearchModel ? undefined : "Inherit chat model"}
          onChange={(event) => changeField("webSearchModel", event.target.value)}
        />
        <InheritanceToggle
          field="Web search model"
          overridden={overrides.webSearchModel}
          onChange={(enabled) => toggleField("webSearchModel", enabled)}
        />
      </div>
      <div className={inheritedClass(overrides.webSearchMaxResults)}>
        <FieldHint label="Web results" text={PROJECT_AI_FIELD_HINTS.webSearchMaxResults} />
        <input
          aria-label="Web results"
          type="number"
          min="1"
          max="20"
          value={form.webSearchMaxResults}
          onChange={(event) => changeField("webSearchMaxResults", event.target.value)}
        />
        <InheritanceToggle
          field="Web results"
          overridden={overrides.webSearchMaxResults}
          onChange={(enabled) => toggleField("webSearchMaxResults", enabled)}
        />
      </div>
      <div className={inheritedClass(overrides.webSearchMaxEvidenceChars)}>
        <FieldHint
          label="Web evidence budget"
          text={PROJECT_AI_FIELD_HINTS.webSearchMaxEvidenceChars}
        />
        <input
          aria-label="Web evidence budget"
          type="number"
          min="500"
          max="100000"
          value={form.webSearchMaxEvidenceChars}
          onChange={(event) => changeField("webSearchMaxEvidenceChars", event.target.value)}
        />
        <InheritanceToggle
          field="Web evidence budget"
          overridden={overrides.webSearchMaxEvidenceChars}
          onChange={(enabled) => toggleField("webSearchMaxEvidenceChars", enabled)}
        />
      </div>
      <div className={inheritedClass(overrides.webSearchMaxOutputTokens)}>
        <FieldHint
          label="Web search tokens"
          text={PROJECT_AI_FIELD_HINTS.webSearchMaxOutputTokens}
        />
        <input
          aria-label="Web search tokens"
          type="number"
          min="256"
          max="32000"
          value={form.webSearchMaxOutputTokens}
          onChange={(event) => changeField("webSearchMaxOutputTokens", event.target.value)}
        />
        <InheritanceToggle
          field="Web search tokens"
          overridden={overrides.webSearchMaxOutputTokens}
          onChange={(enabled) => toggleField("webSearchMaxOutputTokens", enabled)}
        />
      </div>
      <div className={inheritedClass(overrides.webSearchTimeout)}>
        <FieldHint label="Web timeout" text={PROJECT_AI_FIELD_HINTS.webSearchTimeout} />
        <input
          aria-label="Web timeout"
          type="number"
          min="1"
          max="300"
          value={form.webSearchTimeout}
          onChange={(event) => changeField("webSearchTimeout", event.target.value)}
        />
        <InheritanceToggle
          field="Web timeout"
          overridden={overrides.webSearchTimeout}
          onChange={(enabled) => toggleField("webSearchTimeout", enabled)}
        />
      </div>
      <div className={`${inheritedClass(overrides.domain)} field-control--wide`}>
        <FieldHint label="Project instructions" text={PROJECT_AI_FIELD_HINTS.domain} />
        <textarea
          aria-label="Project instructions"
          value={form.domain}
          placeholder="e.g. Answer in Bengali. Use this Project’s official terms."
          onChange={(event) => changeField("domain", event.target.value)}
        />
        <InheritanceToggle
          field="Project instructions"
          overridden={overrides.domain}
          onChange={(enabled) => toggleField("domain", enabled)}
        />
      </div>
      <div
        className={
          form.translation === "inherit"
            ? "field-control field-control--inherited"
            : "field-control"
        }
      >
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
          <option value="inherit">Inherit ({translationHint})</option>
          <option value="on">On</option>
          <option value="off">Off</option>
        </select>
        <InheritanceToggle
          field="Query translation"
          overridden={form.translation !== "inherit"}
          onChange={(overridden) =>
            setForm((current) => ({
              ...current,
              translation: overridden ? translationHint : "inherit",
            }))
          }
        />
      </div>
      <div
        className={
          form.rerankMode === "inherit" ? "field-control field-control--inherited" : "field-control"
        }
      >
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
          <option value="inherit">Inherit ({rerankHint})</option>
          <option value="always">Always</option>
          <option value="cross_language">Cross-language</option>
          <option value="off">Off</option>
        </select>
        <InheritanceToggle
          field="Rerank mode"
          overridden={form.rerankMode !== "inherit"}
          onChange={(overridden) =>
            setForm((current) => ({
              ...current,
              rerankMode: overridden ? rerankHint : "inherit",
            }))
          }
        />
      </div>
      <div className={inheritedClass(overrides.citations)}>
        <FieldHint label="Citations" text={PROJECT_AI_FIELD_HINTS.citations} />
        <label className="check-control">
          <input
            type="checkbox"
            checked={form.citations}
            onChange={(event) => changeField("citations", event.target.checked)}
          />{" "}
          Include citations
        </label>
        <InheritanceToggle
          field="Citations"
          overridden={overrides.citations}
          onChange={(enabled) => toggleField("citations", enabled)}
        />
      </div>
    </div>
  );
}
