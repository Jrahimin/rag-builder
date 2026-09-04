import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, vi } from "vitest";
import {
  operatorApiClient,
  type EffectiveProjectAIConfig,
  type Organization,
  type ProjectAIConfigRevision,
  type ProviderCapability,
  type RAGProfileOption,
} from "../../api/operatorApiClient";
import { OperatorConsoleApp } from "../../app/OperatorConsoleApp";
import { configurationFixture, projectFixture } from "../../test/operatorTestFixtures";
import { renderOperatorComponent } from "../../test/renderOperatorComponent";

const organization: Organization = {
  id: projectFixture.organization_id,
  name: "Acme Client",
  description: null,
  is_active: true,
  deleted_at: null,
  deleted_by: null,
  created_at: "2026-08-16T00:00:00Z",
  updated_at: "2026-08-16T00:00:00Z",
};

const capability: ProviderCapability = {
  provider: "openai",
  model: "o1-test",
  capability_version: "test",
  supports_stream_usage: true,
  parameters: {
    temperature: {
      supported: false,
      wire_name: null,
      minimum: 0,
      maximum: 2,
      omit_when_none: true,
    },
    max_tokens: {
      supported: true,
      wire_name: "max_completion_tokens",
      minimum: 1,
      maximum: 128_000,
      omit_when_none: false,
    },
  },
};

const baseExecution = {
  retrieval_top_k: 10,
  semantic_candidate_top_k: 50,
  keyword_candidate_top_k: 40,
  hnsw_ef_search: 100,
  rrf_k: 60,
  semantic_weight: 1,
  keyword_weight: 1,
  score_threshold: null,
  rerank_mode: "always",
  rerank_candidate_window: 25,
  rerank_score_threshold: null,
  min_ocr_confidence: null,
  max_chunks_per_document: 4,
  max_chunks_per_section: 2,
  deduplicate_by_content_hash: true,
  passage_scoring_enabled: false,
  passage_window_tokens: 96,
  passage_overlap_tokens: 24,
  passage_min_tokens: 32,
  max_related_sources: 8,
  max_relationship_candidates: 20,
  max_context_chunks: 8,
  context_char_budget: 12_000,
  max_history_messages: 10,
};

function executionProfiles(): RAGProfileOption[] {
  return [
    {
      id: "economy",
      certification_status: "certified",
      selectable: true,
      recommended: false,
      profile_hash: "e".repeat(64),
      values: {
        ...baseExecution,
        retrieval_top_k: 6,
        semantic_candidate_top_k: 30,
        keyword_candidate_top_k: 25,
        rerank_candidate_window: 15,
        max_context_chunks: 5,
        context_char_budget: 8_000,
        max_history_messages: 6,
      },
    },
    {
      id: "standard",
      certification_status: "certified",
      selectable: true,
      recommended: true,
      profile_hash: "s".repeat(64),
      values: { ...baseExecution },
    },
    {
      id: "quality",
      certification_status: "certified",
      selectable: true,
      recommended: false,
      profile_hash: "q".repeat(64),
      values: {
        ...baseExecution,
        retrieval_top_k: 18,
        semantic_candidate_top_k: 90,
        keyword_candidate_top_k: 70,
        rerank_candidate_window: 45,
        max_context_chunks: 14,
        context_char_budget: 24_000,
        max_history_messages: 18,
      },
    },
  ] as RAGProfileOption[];
}

function revisionFor(
  configuration: ProjectAIConfigRevision["configuration"],
  id = "22222222-2222-2222-2222-222222222222",
): ProjectAIConfigRevision {
  return {
    id,
    project_id: projectFixture.id,
    revision_number: 1,
    configuration_hash: "b".repeat(64),
    configuration,
    schema_version: 2,
    source: "project_revision",
    reason: "Focused AI configuration test",
    created_by: "test-admin",
    restored_from_revision_id: null,
    created_at: "2026-08-16T00:00:00Z",
  };
}

function mockProjectShell() {
  vi.spyOn(operatorApiClient, "getAllOperatorProjects").mockResolvedValue({
    items: [projectFixture],
    total: 1,
    limit: 100,
    offset: 0,
  });
  vi.spyOn(operatorApiClient, "getOrganizations").mockResolvedValue({
    items: [organization],
    total: 1,
    limit: 100,
    offset: 0,
  });
  vi.spyOn(operatorApiClient, "getProjectOwnershipMigration").mockResolvedValue({
    total_projects: 1,
    locked_projects: 1,
    legacy_unlocked_projects: 0,
    default_organization_unlocked_projects: 0,
    projects: [],
  });
  vi.spyOn(operatorApiClient, "getConfiguration").mockResolvedValue({
    ...configurationFixture,
    llm: { ...configurationFixture.llm, backend: "openai", model: "o1-test" },
  });
}

function effectiveConfig(activeRevisionId: string | null): EffectiveProjectAIConfig {
  const deploymentConfiguration = {
    llm: {
      provider: "openai",
      model: "o1-test",
      temperature: null,
      max_tokens: 2048,
      generation_model_id: "openai-o1-test",
    },
    retrieval: {
      strategy: "hybrid",
      top_k: baseExecution.retrieval_top_k,
      semantic_candidate_top_k: baseExecution.semantic_candidate_top_k,
      keyword_candidate_top_k: baseExecution.keyword_candidate_top_k,
      hnsw_ef_search: baseExecution.hnsw_ef_search,
      rrf_k: baseExecution.rrf_k,
      semantic_weight: baseExecution.semantic_weight,
      keyword_weight: baseExecution.keyword_weight,
      score_threshold: baseExecution.score_threshold,
      rerank_mode: baseExecution.rerank_mode,
      rerank_candidate_window: baseExecution.rerank_candidate_window,
      rerank_score_threshold: baseExecution.rerank_score_threshold,
      min_ocr_confidence: baseExecution.min_ocr_confidence,
      max_chunks_per_document: baseExecution.max_chunks_per_document,
      max_chunks_per_section: baseExecution.max_chunks_per_section,
      deduplicate_by_content_hash: baseExecution.deduplicate_by_content_hash,
      semantic_evidence_score_threshold: 0.5,
      passage_scoring_enabled: baseExecution.passage_scoring_enabled,
      passage_window_tokens: baseExecution.passage_window_tokens,
      passage_overlap_tokens: baseExecution.passage_overlap_tokens,
      passage_min_tokens: baseExecution.passage_min_tokens,
      max_related_sources: baseExecution.max_related_sources,
      max_relationship_candidates: baseExecution.max_relationship_candidates,
      query_translation_enabled: false,
    },
    chat: {
      response_mode: "indexed_only",
      grounding_mode: "strict",
      max_context_chunks: baseExecution.max_context_chunks,
      context_char_budget: baseExecution.context_char_budget,
      max_history_messages: baseExecution.max_history_messages,
      include_citations: true,
      citation_excerpt_max_chars: 500,
      evidence_gate_mode: "enforce",
      lexical_corroboration_floor_score: 0.35,
      lexical_corroboration_coverage: 0.2,
      minimum_claim_token_coverage: 0.2,
      minimum_reranker_evidence_score: 0.4,
    },
    web_search: {
      enabled: true,
      backend: "openai",
      model: "o1-test",
      max_results: 8,
      max_evidence_chars: 12_000,
      max_output_tokens: 4096,
      request_timeout_seconds: 45,
    },
    domain_instructions: "",
    prompt_profile: "default",
    prompt_version: "v1",
    source_policy_mode: "off" as const,
  } as unknown as EffectiveProjectAIConfig["configuration"];

  return {
    project_id: projectFixture.id,
    active_revision_id: activeRevisionId,
    configuration_hash: "a".repeat(64),
    configuration: deploymentConfiguration,
    deployment_configuration: deploymentConfiguration,
    effective_value_hash: "d".repeat(64),
    resolution_fingerprint: "e".repeat(64),
    required_index_action: "none",
    structured_origins: {},
    invariants: {},
    base_profile_id: null,
    custom_execution: false,
    compatibility_warnings: [],
    allowed_generation_models: [
      { id: "openai-o1-test", provider: "openai", model: "o1-test" },
      { id: "openai-gpt-4o-mini", provider: "openai", model: "gpt-4o-mini" },
    ],
    rag_profiles: executionProfiles(),
    origins: {},
    provenance: {
      project_config_revision_id: activeRevisionId,
      project_config_revision_number: activeRevisionId ? 1 : null,
      project_config_hash: activeRevisionId ? "b".repeat(64) : null,
      global_config_fingerprint: "c".repeat(64),
      provider_capability_version: "test",
      prompt_versions: { chat: "v1", profile: "default" },
      configured_source_policy_mode: "off",
      effective_source_policy_mode: "off",
      source_policy_deployment_cap: "enforce",
      deployment_default_execution_profile_id: "standard",
    },
  } as unknown as EffectiveProjectAIConfig;
}

function setupAI(config: EffectiveProjectAIConfig, stored?: ProjectAIConfigRevision) {
  vi.spyOn(operatorApiClient, "getProjectAIConfig").mockResolvedValue(config);
  vi.spyOn(operatorApiClient, "getProjectAIConfigHistory").mockResolvedValue(
    stored ? [stored] : [],
  );
  vi.spyOn(operatorApiClient, "getProviderCapabilities").mockResolvedValue([capability]);
  return vi.spyOn(operatorApiClient, "createProjectAIConfig").mockResolvedValue(revisionFor({}));
}

async function saveRevision(reason = "Focused transition") {
  await userEvent.type(await screen.findByLabelText("Revision reason"), reason);
  await userEvent.click(screen.getByRole("button", { name: "Create and activate revision" }));
}

afterEach(() => vi.restoreAllMocks());

test("uses canonical Project administration and shows locked Organization ownership", async () => {
  mockProjectShell();
  renderOperatorComponent(<OperatorConsoleApp />, `/projects?project=${projectFixture.id}`);
  expect(
    await screen.findByRole("heading", { name: projectFixture.name, level: 2 }, { timeout: 5_000 }),
  ).toBeInTheDocument();
  expect(screen.getByText("Acme Client")).toBeInTheDocument();
  expect(screen.getByText("Immutable")).toBeInTheDocument();
});

test("preset selection updates all mapped core execution values", async () => {
  mockProjectShell();
  setupAI(effectiveConfig(null));
  renderOperatorComponent(
    <OperatorConsoleApp />,
    `/projects?project=${projectFixture.id}&section=ai-config`,
  );

  await userEvent.click(await screen.findByRole("radio", { name: "Quality RAG profile" }));
  expect(screen.getByLabelText("Semantic candidates")).toHaveValue(90);
  expect(screen.getByLabelText("Keyword candidates")).toHaveValue(70);
  expect(screen.getByLabelText("Rerank window")).toHaveValue(45);
  expect(screen.getByLabelText("Top K")).toHaveValue(18);
  expect(screen.getByLabelText("Context chunks")).toHaveValue(14);
  expect(screen.getByLabelText("Context budget")).toHaveValue(24_000);
  expect(screen.getByLabelText("History messages")).toHaveValue(18);
});

test("editing a preset materializes a complete Custom bundle", async () => {
  mockProjectShell();
  const create = setupAI(effectiveConfig(null));
  renderOperatorComponent(
    <OperatorConsoleApp />,
    `/projects?project=${projectFixture.id}&section=ai-config`,
  );

  await userEvent.click(await screen.findByRole("radio", { name: "Standard RAG profile" }));
  fireEvent.change(screen.getByLabelText("Top K"), { target: { value: "12" } });
  expect(screen.getByText("Custom · based on Standard · 1 setting changed")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Reset to Standard" })).toBeInTheDocument();
  await saveRevision();

  await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
  expect(create.mock.calls[0]?.[1].execution).toMatchObject({
    ...baseExecution,
    profile_id: "custom",
    retrieval_top_k: 12,
  });
});

test("editing Global execution preserves Global lineage", async () => {
  mockProjectShell();
  setupAI(effectiveConfig(null));
  renderOperatorComponent(
    <OperatorConsoleApp />,
    `/projects?project=${projectFixture.id}&section=ai-config`,
  );

  fireEvent.change(await screen.findByLabelText("Top K"), { target: { value: "12" } });
  expect(
    screen.getByText("Custom · based on Global (Standard) · 1 setting changed"),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Reset to Global" })).toBeInTheDocument();
  expect(
    screen.queryByText("Custom · based on Standard · 1 setting changed"),
  ).not.toBeInTheDocument();
});

test("reverting Custom execution values restores the previous profile", async () => {
  mockProjectShell();
  const create = setupAI(effectiveConfig(null));
  renderOperatorComponent(
    <OperatorConsoleApp />,
    `/projects?project=${projectFixture.id}&section=ai-config`,
  );

  fireEvent.change(await screen.findByLabelText("Semantic candidates"), { target: { value: "5" } });
  expect(
    screen.getByText("Custom · based on Global (Standard) · 1 setting changed"),
  ).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Keyword candidates"), { target: { value: "7" } });
  expect(
    screen.getByText("Custom · based on Global (Standard) · 2 settings changed"),
  ).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Keyword candidates"), { target: { value: "40" } });
  expect(
    screen.getByText("Custom · based on Global (Standard) · 1 setting changed"),
  ).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Semantic candidates"), { target: { value: "50" } });
  expect(screen.queryByText(/Custom · based on/)).not.toBeInTheDocument();
  expect(screen.getByRole("radio", { name: "Global RAG profile" })).toBeChecked();
  expect(screen.queryByText("Unsaved")).not.toBeInTheDocument();

  await userEvent.click(screen.getByRole("radio", { name: "Quality RAG profile" }));
  fireEvent.change(screen.getByLabelText("Top K"), { target: { value: "13" } });
  expect(screen.getByText(/Custom · based on Quality · 1 setting changed/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Top K"), { target: { value: "18" } });
  expect(screen.queryByText(/Custom · based on/)).not.toBeInTheDocument();
  expect(screen.getByRole("radio", { name: "Quality RAG profile" })).toBeChecked();
  await saveRevision();

  await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
  expect(create.mock.calls[0]?.[1].execution).toEqual({ profile_id: "quality" });
});

test("reselecting a preset discards Custom values", async () => {
  mockProjectShell();
  const create = setupAI(effectiveConfig(null));
  renderOperatorComponent(
    <OperatorConsoleApp />,
    `/projects?project=${projectFixture.id}&section=ai-config`,
  );

  await userEvent.click(await screen.findByRole("radio", { name: "Quality RAG profile" }));
  fireEvent.change(screen.getByLabelText("Top K"), { target: { value: "13" } });
  expect(screen.getByText(/Custom · based on Quality/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("radio", { name: "Quality RAG profile" }));
  expect(screen.getByLabelText("Top K")).toHaveValue(18);
  await saveRevision();

  await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
  expect(create.mock.calls[0]?.[1].execution).toEqual({ profile_id: "quality" });
});

test("behavior changes do not change the selected RAG profile", async () => {
  mockProjectShell();
  const create = setupAI(effectiveConfig(null));
  renderOperatorComponent(
    <OperatorConsoleApp />,
    `/projects?project=${projectFixture.id}&section=ai-config`,
  );

  const standard = await screen.findByRole("radio", { name: "Standard RAG profile" });
  await userEvent.click(standard);
  await userEvent.selectOptions(screen.getByLabelText("Response mode"), "indexed_then_web");
  expect(standard).toBeChecked();
  expect(screen.getByRole("button", { name: "Response mode: Use Global" })).toBeInTheDocument();
  expect(screen.queryByText(/Custom · based on/)).not.toBeInTheDocument();
  await saveRevision();

  await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
  expect(create.mock.calls[0]?.[1]).toMatchObject({
    behavior: { response_mode: "indexed_then_web" },
    execution: { profile_id: "standard" },
  });
});

test("selecting the Global behavior option restores Global source", async () => {
  mockProjectShell();
  const create = setupAI(effectiveConfig(null));
  renderOperatorComponent(
    <OperatorConsoleApp />,
    `/projects?project=${projectFixture.id}&section=ai-config`,
  );

  await userEvent.click(await screen.findByRole("radio", { name: "Standard RAG profile" }));
  await userEvent.selectOptions(screen.getByLabelText("Response mode"), "indexed_then_web");
  expect(screen.getByRole("button", { name: "Response mode: Use Global" })).toBeInTheDocument();
  await userEvent.selectOptions(screen.getByLabelText("Response mode"), "indexed_only");
  expect(
    screen.queryByRole("button", { name: "Response mode: Use Global" }),
  ).not.toBeInTheDocument();
  await saveRevision();

  await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
  expect(create.mock.calls[0]?.[1]).toMatchObject({
    behavior: {},
    execution: { profile_id: "standard" },
  });
});

test("clearing domain instructions back to Global restores Global source", async () => {
  mockProjectShell();
  setupAI(effectiveConfig(null));
  renderOperatorComponent(
    <OperatorConsoleApp />,
    `/projects?project=${projectFixture.id}&section=ai-config`,
  );

  const domain = await screen.findByLabelText("Domain instructions");
  await userEvent.type(domain, "Tax audit focus");
  expect(
    screen.getByRole("button", { name: "Domain instructions: Use Global" }),
  ).toBeInTheDocument();
  await userEvent.clear(domain);
  expect(
    screen.queryByRole("button", { name: "Domain instructions: Use Global" }),
  ).not.toBeInTheDocument();
  expect(screen.getByText("Follows Global")).toBeInTheDocument();
  expect(screen.queryByText("Unsaved")).not.toBeInTheDocument();
});

test("an explicit Project value remains distinct when it equals Global", async () => {
  mockProjectShell();
  const stored = revisionFor({
    behavior: { response_mode: "indexed_only" },
    execution: {},
  });
  const create = setupAI(effectiveConfig(stored.id), stored);
  renderOperatorComponent(
    <OperatorConsoleApp />,
    `/projects?project=${projectFixture.id}&section=ai-config`,
  );

  expect(await screen.findByLabelText("Response mode")).toHaveValue("indexed_only");
  expect(screen.getByRole("button", { name: "Response mode: Use Global" })).toBeInTheDocument();
  await saveRevision();

  await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
  expect(create.mock.calls[0]?.[1].behavior).toEqual({ response_mode: "indexed_only" });
});

test("Use Global removes an existing behavior override", async () => {
  mockProjectShell();
  const stored = revisionFor({
    behavior: { response_mode: "indexed_only", translation_policy: "inherit" },
    execution: { profile_id: "standard" },
  });
  const create = setupAI(effectiveConfig(stored.id), stored);
  renderOperatorComponent(
    <OperatorConsoleApp />,
    `/projects?project=${projectFixture.id}&section=ai-config`,
  );

  expect(await screen.findByLabelText("Response mode")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Response mode: Use Global" }));
  expect(screen.getByLabelText("Response mode")).toHaveValue("indexed_only");
  expect(
    screen.queryByRole("button", { name: "Response mode: Use Global" }),
  ).not.toBeInTheDocument();
  await saveRevision();

  await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
  expect(create.mock.calls[0]?.[1]).toEqual({
    behavior: {},
    execution: { profile_id: "standard" },
  });
});

test("one-model generation UI has no redundant selector", async () => {
  mockProjectShell();
  const config = effectiveConfig(null);
  config.allowed_generation_models = [
    { id: "openai-o1-test", provider: "openai", model: "o1-test" },
  ];
  setupAI(config);
  renderOperatorComponent(
    <OperatorConsoleApp />,
    `/projects?project=${projectFixture.id}&section=ai-config`,
  );

  expect(await screen.findByText("openai-o1-test")).toBeInTheDocument();
  expect(screen.queryByLabelText("Generation model")).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Generation model: Use Global" }),
  ).not.toBeInTheDocument();
});

test("sparse payload contains only explicitly overridden behavior", async () => {
  mockProjectShell();
  const create = setupAI(effectiveConfig(null));
  renderOperatorComponent(
    <OperatorConsoleApp />,
    `/projects?project=${projectFixture.id}&section=ai-config`,
  );

  await userEvent.selectOptions(await screen.findByLabelText("Query translation"), "enabled");
  expect(screen.getByRole("button", { name: "Query translation: Use Global" })).toBeInTheDocument();
  await saveRevision();

  await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
  expect(create.mock.calls[0]?.[1]).toEqual({
    behavior: { translation_policy: "enabled" },
    execution: {},
  });
});

test("origin summary reports Custom execution instead of a leaf override count", async () => {
  mockProjectShell();
  const stored = revisionFor({
    behavior: { response_mode: "indexed_then_web" },
    execution: { profile_id: "custom", ...baseExecution, retrieval_top_k: 12 },
  });
  const config = effectiveConfig(stored.id);
  config.custom_execution = true;
  config.origins = {
    "llm.generation_model_id": "project",
    "chat.response_mode": "project",
    "chat.grounding_mode": "project",
    "retrieval.top_k": "custom_profile",
    "retrieval.semantic_candidate_top_k": "custom_profile",
    "retrieval.keyword_candidate_top_k": "custom_profile",
    "chat.max_context_chunks": "custom_profile",
    "chat.include_citations": "code_invariant",
    "retrieval.strategy": "code_invariant",
  };
  setupAI(config, stored);
  renderOperatorComponent(
    <OperatorConsoleApp />,
    `/projects?project=${projectFixture.id}&section=ai-config`,
  );

  expect(
    await screen.findByRole("button", { name: /Custom execution · 3 behavior overrides/ }),
  ).toBeInTheDocument();
  expect(screen.queryByText(/\d+ Project overrides/)).not.toBeInTheDocument();
});
