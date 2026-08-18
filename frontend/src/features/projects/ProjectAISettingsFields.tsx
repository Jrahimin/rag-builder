import type {
  EffectiveProjectAIConfig,
  ProjectAIConfig,
  ProviderCapability,
} from "../../api/operatorApiClient";

export type TranslationMode = "inherit" | "on" | "off";
export type RerankModeChoice = "inherit" | "always" | "cross_language" | "off";

export type ProjectConfigForm = {
  provider: string;
  model: string;
  temperature: string;
  maxTokens: string;
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
  domain: false,
  topK: false,
  strategy: false,
  evidence: false,
  citations: false,
  sourcePolicy: false,
};

export const emptyProjectConfigForm: ProjectConfigForm = {
  provider: "echo",
  model: "",
  temperature: "",
  maxTokens: "",
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
): ProjectConfigForm {
  return {
    provider: config.configuration.llm.provider,
    model: config.configuration.llm.model,
    temperature:
      config.configuration.llm.temperature === null
        ? ""
        : String(config.configuration.llm.temperature),
    maxTokens: String(config.configuration.llm.max_tokens),
    domain: config.configuration.domain_instructions,
    topK: String(config.configuration.retrieval.top_k),
    strategy: config.configuration.retrieval.strategy,
    translation: storedTranslationMode(stored),
    rerankMode: storedRerankMode(stored),
    evidence: String(config.configuration.retrieval.semantic_evidence_score_threshold),
    citations: config.configuration.chat.include_citations,
    sourcePolicy: config.configuration.source_policy_mode,
    reason: "",
  };
}

export function configOverridesFromStored(stored: ProjectAIConfig): ProjectConfigOverrides {
  return {
    provider: hasValue(stored.llm, "provider"),
    model: hasValue(stored.llm, "model"),
    temperature: hasValue(stored.llm, "temperature") && stored.llm?.temperature !== null,
    maxTokens: hasValue(stored.llm, "max_tokens"),
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

  setSparseValue(llm, "provider", overrides.provider, form.provider);
  setSparseValue(llm, "model", overrides.model, form.model);
  setSparseValue(
    llm,
    "temperature",
    overrides.temperature && capability?.parameters.temperature.supported !== false,
    form.temperature === "" ? null : Number(form.temperature),
  );
  setSparseValue(llm, "max_tokens", overrides.maxTokens, Number(form.maxTokens));
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
  return configuration;
}

export function sparseHasOverrides(configuration: ProjectAIConfig): boolean {
  const llm = configuration.llm ?? {};
  const retrieval = configuration.retrieval ?? {};
  const chat = configuration.chat ?? {};
  return (
    Object.keys(llm).length > 0 ||
    Object.keys(retrieval).length > 0 ||
    Object.keys(chat).length > 0 ||
    configuration.domain_instructions != null ||
    configuration.prompt_profile != null ||
    configuration.prompt_version != null ||
    configuration.source_policy_mode != null
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
    <label className="check-control">
      <input
        aria-label={`${field}: inherit global`}
        type="checkbox"
        checked={!overridden}
        disabled={disabled}
        onChange={(event) => onChange(!event.target.checked)}
      />{" "}
      Inherit global
    </label>
  );
}

export function ProjectAISettingsFields({
  form,
  setForm,
  overrides,
  setOverride,
  effective,
}: {
  form: ProjectConfigForm;
  setForm: (form: ProjectConfigForm) => void;
  overrides: ProjectConfigOverrides;
  setOverride: (key: ProjectConfigOverride, enabled: boolean) => void;
  effective?: EffectiveProjectAIConfig | null;
}) {
  const translationHint = effective?.configuration.retrieval.query_translation_enabled
    ? "on"
    : "off";
  const rerankHint = effective?.configuration.retrieval.rerank_mode ?? "always";
  return (
    <div className="form-grid">
      <div className="field-control">
        <span>Provider</span>
        <select
          aria-label="Provider"
          value={form.provider}
          disabled={!overrides.provider}
          onChange={(event) => setForm({ ...form, provider: event.target.value })}
        >
          {["echo", "openai", "openai_compatible", "ollama", "gemini"].map((value) => (
            <option key={value}>{value}</option>
          ))}
        </select>
        <InheritanceToggle
          field="Provider"
          overridden={overrides.provider}
          onChange={(enabled) => setOverride("provider", enabled)}
        />
      </div>
      <div className="field-control">
        <span>Model</span>
        <input
          aria-label="Model"
          value={form.model}
          disabled={!overrides.model}
          onChange={(event) => setForm({ ...form, model: event.target.value })}
        />
        <InheritanceToggle
          field="Model"
          overridden={overrides.model}
          onChange={(enabled) => setOverride("model", enabled)}
        />
      </div>
      <div className="field-control field-control--wide">
        <span>Domain instructions</span>
        <textarea
          aria-label="Domain instructions"
          value={form.domain}
          disabled={!overrides.domain}
          onChange={(event) => setForm({ ...form, domain: event.target.value })}
        />
        <InheritanceToggle
          field="Domain instructions"
          overridden={overrides.domain}
          onChange={(enabled) => setOverride("domain", enabled)}
        />
      </div>
      <div className="field-control">
        <span>Query translation</span>
        <select
          aria-label="Query translation"
          value={form.translation}
          onChange={(event) =>
            setForm({ ...form, translation: event.target.value as TranslationMode })
          }
        >
          <option value="inherit">Inherit ({translationHint})</option>
          <option value="on">On</option>
          <option value="off">Off</option>
        </select>
      </div>
      <div className="field-control">
        <span>Rerank mode</span>
        <select
          aria-label="Rerank mode"
          value={form.rerankMode}
          onChange={(event) =>
            setForm({ ...form, rerankMode: event.target.value as RerankModeChoice })
          }
        >
          <option value="inherit">Inherit ({rerankHint})</option>
          <option value="always">Always</option>
          <option value="cross_language">Cross-language</option>
          <option value="off">Off</option>
        </select>
      </div>
      <div className="field-control">
        <span>Citations</span>
        <label className="check-control">
          <input
            type="checkbox"
            checked={form.citations}
            disabled={!overrides.citations}
            onChange={(event) => setForm({ ...form, citations: event.target.checked })}
          />{" "}
          Include citations
        </label>
        <InheritanceToggle
          field="Citations"
          overridden={overrides.citations}
          onChange={(enabled) => setOverride("citations", enabled)}
        />
      </div>
    </div>
  );
}
