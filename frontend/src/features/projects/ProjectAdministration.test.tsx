import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, vi } from "vitest";
import {
  operatorApiClient,
  type Document,
  type EffectiveProjectAIConfig,
  type Organization,
  type ProjectAIConfigRevision,
  type ProviderCapability,
  type SourceState,
} from "../../api/operatorApiClient";
import { OperatorConsoleApp } from "../../app/OperatorConsoleApp";
import { projectFixture, configurationFixture } from "../../test/operatorTestFixtures";
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

afterEach(() => vi.restoreAllMocks());

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
    llm: {
      ...configurationFixture.llm,
      backend: "openai",
      model: "o1-test",
    },
  });
}

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
      top_k: 10,
      rerank_mode: "always",
      rerank_candidate_window: 25,
      rerank_score_threshold: null,
      semantic_evidence_score_threshold: 0.5,
      passage_scoring_enabled: false,
      passage_window_tokens: 96,
      passage_overlap_tokens: 24,
      passage_min_tokens: 32,
      query_translation_enabled: false,
    },
    chat: {
      response_mode: "indexed_only",
      grounding_mode: "strict",
      max_context_chunks: 8,
      context_char_budget: 12_000,
      max_history_messages: 10,
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
    custom_execution: true,
    compatibility_warnings: [],
    allowed_generation_models: [
      { id: "openai-o1-test", provider: "openai", model: "o1-test" },
      { id: "openai-gpt-4o-mini", provider: "openai", model: "gpt-4o-mini" },
    ],
    rag_profiles: [
      {
        id: "standard",
        certification_status: "candidate",
        selectable: false,
        recommended: false,
        profile_hash: "f".repeat(64),
        values: {},
      },
    ],
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
    },
  } as unknown as EffectiveProjectAIConfig;
}

test("uses canonical Project administration and shows locked Organization ownership", async () => {
  mockProjectShell();

  renderOperatorComponent(<OperatorConsoleApp />, `/projects?project=${projectFixture.id}`);
  expect(
    await screen.findByRole("heading", { name: projectFixture.name, level: 2 }, { timeout: 5_000 }),
  ).toBeInTheDocument();
  expect(screen.getByText("Acme Client")).toBeInTheDocument();
  expect(screen.getByText("Immutable")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Create test project/i })).not.toBeInTheDocument();
});

test("keeps Custom execution values explicit when one value changes", async () => {
  mockProjectShell();
  const revision: ProjectAIConfigRevision = {
    id: "22222222-2222-2222-2222-222222222222",
    project_id: projectFixture.id,
    revision_number: 1,
    configuration_hash: "b".repeat(64),
    configuration: {
      execution: { profile_id: "custom", retrieval_top_k: 23, max_context_chunks: 7 },
    },
    schema_version: 2,
    source: "project_revision",
    reason: "Initial override",
    created_by: "test-admin",
    restored_from_revision_id: null,
    created_at: "2026-08-16T00:00:00Z",
  };
  const config = effectiveConfig(revision.id);
  config.configuration = {
    ...config.configuration,
    retrieval: { ...config.configuration.retrieval, top_k: 23 },
  };
  vi.spyOn(operatorApiClient, "getProjectAIConfig").mockResolvedValue(config);
  vi.spyOn(operatorApiClient, "getProjectAIConfigHistory").mockResolvedValue([revision]);
  vi.spyOn(operatorApiClient, "getProviderCapabilities").mockResolvedValue([capability]);
  const create = vi.spyOn(operatorApiClient, "createProjectAIConfig").mockResolvedValue(revision);

  renderOperatorComponent(
    <OperatorConsoleApp />,
    `/projects?project=${projectFixture.id}&section=ai-config`,
  );

  fireEvent.change(await screen.findByLabelText("Top K"), { target: { value: "12" } });
  await userEvent.type(screen.getByLabelText("Revision reason"), "Tune Custom top K");
  await userEvent.click(screen.getByRole("button", { name: "Create and activate revision" }));

  await waitFor(() => expect(create).toHaveBeenCalled());
  const firstCreateCall = create.mock.calls[0];
  if (!firstCreateCall) {
    throw new Error("Expected a project AI-config revision to be created");
  }
  const saved = firstCreateCall[1];
  expect(saved.execution).toEqual({
    profile_id: "custom",
    retrieval_top_k: 12,
    max_context_chunks: 7,
  });
  expect(saved.behavior).toEqual({});
});

test("treats V2 Project values equal to deployment defaults as inherited", async () => {
  mockProjectShell();
  const revision: ProjectAIConfigRevision = {
    id: "33333333-3333-3333-3333-333333333333",
    project_id: projectFixture.id,
    revision_number: 1,
    configuration_hash: "d".repeat(64),
    configuration: {
      behavior: { response_mode: "indexed_only", translation_policy: "disabled" },
      execution: { retrieval_top_k: 10, rerank_mode: "always" },
    },
    schema_version: 2,
    source: "project_revision",
    reason: "Redundant legacy values",
    created_by: "test-admin",
    restored_from_revision_id: null,
    created_at: "2026-08-16T00:00:00Z",
  };
  vi.spyOn(operatorApiClient, "getProjectAIConfig").mockResolvedValue(effectiveConfig(revision.id));
  vi.spyOn(operatorApiClient, "getProjectAIConfigHistory").mockResolvedValue([revision]);
  vi.spyOn(operatorApiClient, "getProviderCapabilities").mockResolvedValue([capability]);

  renderOperatorComponent(
    <OperatorConsoleApp />,
    `/projects?project=${projectFixture.id}&section=ai-config`,
  );

  expect(await screen.findByLabelText("Response mode: inherit global")).not.toBeChecked();
  expect(screen.getByLabelText("Query translation")).toHaveValue("disabled");
  expect(screen.getByLabelText("Rerank mode")).toHaveValue("always");
});

test("does not create an AI revision when every control inherits", async () => {
  mockProjectShell();
  vi.spyOn(operatorApiClient, "getProjectAIConfig").mockResolvedValue(effectiveConfig(null));
  vi.spyOn(operatorApiClient, "getProjectAIConfigHistory").mockResolvedValue([]);
  vi.spyOn(operatorApiClient, "getProviderCapabilities").mockResolvedValue([capability]);
  const create = vi.spyOn(operatorApiClient, "createProjectAIConfig");

  renderOperatorComponent(
    <OperatorConsoleApp />,
    `/projects?project=${projectFixture.id}&section=ai-config`,
  );

  await userEvent.type(
    await screen.findByLabelText("Revision reason"),
    "Should not create a revision",
  );
  await userEvent.click(screen.getByRole("button", { name: "Create and activate revision" }));

  expect(create).not.toHaveBeenCalled();
  expect(await screen.findByText(/All fields inherit deployment defaults/i)).toBeInTheDocument();
});

test("saves query translation and rerank mode as sparse Project overrides", async () => {
  mockProjectShell();
  vi.spyOn(operatorApiClient, "getProjectAIConfig").mockResolvedValue(effectiveConfig(null));
  vi.spyOn(operatorApiClient, "getProjectAIConfigHistory").mockResolvedValue([]);
  vi.spyOn(operatorApiClient, "getProviderCapabilities").mockResolvedValue([capability]);
  const create = vi.spyOn(operatorApiClient, "createProjectAIConfig").mockResolvedValue({
    id: "22222222-2222-2222-2222-222222222222",
    project_id: projectFixture.id,
    revision_number: 1,
    configuration_hash: "b".repeat(64),
    configuration: {
      behavior: { translation_policy: "enabled" },
      execution: { rerank_mode: "cross_language" },
    },
    schema_version: 2,
    source: "project_revision",
    reason: "Enable multilingual retrieval",
    created_by: "test-admin",
    restored_from_revision_id: null,
    created_at: "2026-08-16T00:00:00Z",
  });

  renderOperatorComponent(
    <OperatorConsoleApp />,
    `/projects?project=${projectFixture.id}&section=ai-config`,
  );

  await userEvent.selectOptions(await screen.findByLabelText("Query translation"), "enabled");
  await userEvent.selectOptions(screen.getByLabelText("Rerank mode"), "cross_language");
  await userEvent.type(screen.getByLabelText("Revision reason"), "Enable multilingual retrieval");
  await userEvent.click(screen.getByRole("button", { name: "Create and activate revision" }));

  await waitFor(() => expect(create).toHaveBeenCalled());
  const saved = create.mock.calls[0]?.[1];
  expect(saved?.behavior).toMatchObject({
    translation_policy: "enabled",
  });
  expect(saved?.execution).toMatchObject({
    rerank_mode: "cross_language",
  });
  expect(saved?.execution).not.toHaveProperty("rerank_enabled");
});

test("drives web behavior through the sparse response mode", async () => {
  mockProjectShell();
  vi.spyOn(operatorApiClient, "getProjectAIConfig").mockResolvedValue(effectiveConfig(null));
  vi.spyOn(operatorApiClient, "getProjectAIConfigHistory").mockResolvedValue([]);
  vi.spyOn(operatorApiClient, "getProviderCapabilities").mockResolvedValue([capability]);
  const create = vi.spyOn(operatorApiClient, "createProjectAIConfig").mockResolvedValue({
    id: "22222222-2222-2222-2222-222222222222",
    project_id: projectFixture.id,
    revision_number: 1,
    configuration_hash: "b".repeat(64),
    configuration: {
      behavior: { response_mode: "indexed_then_web", translation_policy: "disabled" },
    },
    schema_version: 2,
    source: "project_revision",
    reason: "Allow bounded web fallback",
    created_by: "test-admin",
    restored_from_revision_id: null,
    created_at: "2026-08-16T00:00:00Z",
  });

  renderOperatorComponent(
    <OperatorConsoleApp />,
    `/projects?project=${projectFixture.id}&section=ai-config`,
  );

  await userEvent.selectOptions(await screen.findByLabelText("Response mode"), "indexed_then_web");
  await userEvent.type(screen.getByLabelText("Revision reason"), "Allow bounded web fallback");
  await userEvent.click(screen.getByRole("button", { name: "Create and activate revision" }));

  await waitFor(() => expect(create).toHaveBeenCalled());
  expect(create.mock.calls[0]?.[1].behavior).toEqual({ response_mode: "indexed_then_web" });
  expect(screen.queryByLabelText("Web results")).not.toBeInTheDocument();
});

test("keeps a created Project when optional AI settings fail to save", async () => {
  mockProjectShell();
  vi.spyOn(operatorApiClient, "getProjectAIConfig").mockResolvedValue(effectiveConfig(null));
  vi.spyOn(operatorApiClient, "getProjectAIConfigHistory").mockResolvedValue([]);
  vi.spyOn(operatorApiClient, "getProviderCapabilities").mockResolvedValue([capability]);
  const createProject = vi
    .spyOn(operatorApiClient, "createProject")
    .mockResolvedValue(projectFixture);
  const createConfig = vi
    .spyOn(operatorApiClient, "createProjectAIConfig")
    .mockRejectedValue(new Error("revision conflict"));

  renderOperatorComponent(<OperatorConsoleApp />, `/projects?project=${projectFixture.id}`);
  await userEvent.click(await screen.findByRole("button", { name: "Create Project" }));
  await userEvent.type(screen.getByLabelText("Project name"), "Pilot");
  await userEvent.click(screen.getByText("Optional AI settings"));
  await userEvent.selectOptions(screen.getByLabelText("Rerank mode"), "cross_language");
  await userEvent.click(screen.getByRole("button", { name: "Create" }));

  await waitFor(() => expect(createProject).toHaveBeenCalled());
  await waitFor(() => expect(createConfig).toHaveBeenCalled());
  expect(
    await screen.findByText(/Project created. AI settings were not saved/i),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Open AI configuration" })).toBeInTheDocument();
});

test("persists a translation-only sparse override during Project creation", async () => {
  mockProjectShell();
  const createProject = vi
    .spyOn(operatorApiClient, "createProject")
    .mockResolvedValue(projectFixture);
  const createConfig = vi.spyOn(operatorApiClient, "createProjectAIConfig").mockResolvedValue({
    id: "22222222-2222-2222-2222-222222222222",
    project_id: projectFixture.id,
    revision_number: 1,
    configuration_hash: "b".repeat(64),
    configuration: { behavior: { translation_policy: "enabled" }, execution: {} },
    schema_version: 2,
    source: "project_revision",
    reason: "Initial Project AI settings",
    created_by: "test-admin",
    restored_from_revision_id: null,
    created_at: "2026-08-16T00:00:00Z",
  });

  renderOperatorComponent(<OperatorConsoleApp />, `/projects?project=${projectFixture.id}`);
  await userEvent.click(await screen.findByRole("button", { name: "Create Project" }));
  await userEvent.type(screen.getByLabelText("Project name"), "Translation only");
  await userEvent.click(screen.getByText("Optional AI settings"));
  await userEvent.selectOptions(screen.getByLabelText("Query translation"), "enabled");
  await userEvent.click(screen.getByRole("button", { name: "Create" }));

  await waitFor(() => expect(createProject).toHaveBeenCalled());
  await waitFor(() => expect(createConfig).toHaveBeenCalledTimes(1));
  expect(createConfig.mock.calls[0]?.[1]).toEqual({
    behavior: { translation_policy: "enabled" },
    execution: {},
  });
});

test("editing a preset materializes Custom and reselecting the preset resets it", async () => {
  mockProjectShell();
  const config = effectiveConfig(null);
  config.custom_execution = false;
  config.rag_profiles = [
    {
      id: "standard",
      certification_status: "certified",
      selectable: true,
      recommended: true,
      profile_hash: "f".repeat(64),
      values: { retrieval_top_k: 10, rerank_mode: "always" },
    },
  ];
  vi.spyOn(operatorApiClient, "getProjectAIConfig").mockResolvedValue(config);
  vi.spyOn(operatorApiClient, "getProjectAIConfigHistory").mockResolvedValue([]);
  vi.spyOn(operatorApiClient, "getProviderCapabilities").mockResolvedValue([capability]);
  const create = vi.spyOn(operatorApiClient, "createProjectAIConfig").mockResolvedValue({
    id: "22222222-2222-2222-2222-222222222222",
    project_id: projectFixture.id,
    revision_number: 1,
    configuration_hash: "b".repeat(64),
    configuration: {
      behavior: { translation_policy: "inherit" },
      execution: { profile_id: "standard" },
    },
    schema_version: 2,
    source: "project_revision",
    reason: "Use balanced profile",
    created_by: "test-admin",
    restored_from_revision_id: null,
    created_at: "2026-08-16T00:00:00Z",
  });

  renderOperatorComponent(
    <OperatorConsoleApp />,
    `/projects?project=${projectFixture.id}&section=ai-config`,
  );

  await userEvent.selectOptions(await screen.findByLabelText("RAG profile"), "standard");
  expect(screen.getByTestId("rag-profile-state")).toHaveTextContent("standard profile");
  fireEvent.change(screen.getByLabelText("Top K"), { target: { value: "11" } });
  expect(screen.getByTestId("rag-profile-state")).toHaveTextContent(
    "Custom — individual execution settings",
  );
  fireEvent.change(screen.getByLabelText("Top K"), { target: { value: "10" } });
  expect(screen.getByTestId("rag-profile-state")).toHaveTextContent("Custom");
  await userEvent.selectOptions(screen.getByLabelText("RAG profile"), "standard");
  expect(screen.getByTestId("rag-profile-state")).toHaveTextContent("standard profile");

  await userEvent.type(screen.getByLabelText("Revision reason"), "Use balanced profile");
  await userEvent.click(screen.getByRole("button", { name: "Create and activate revision" }));
  await waitFor(() => expect(create).toHaveBeenCalled());
  expect(create.mock.calls[0]?.[1].execution).toEqual({ profile_id: "standard" });
});

test("unsaved preset changes materialize the currently selected preset as Custom", async () => {
  mockProjectShell();
  const config = effectiveConfig(null);
  config.rag_profiles = [
    {
      id: "standard",
      certification_status: "candidate",
      selectable: true,
      recommended: true,
      profile_hash: "f".repeat(64),
      values: { retrieval_top_k: 10, semantic_candidate_top_k: 50, rerank_mode: "always" },
    },
    {
      id: "quality",
      certification_status: "candidate",
      selectable: true,
      recommended: false,
      profile_hash: "q".repeat(64),
      values: { retrieval_top_k: 12, semantic_candidate_top_k: 80, rerank_mode: "always" },
    },
  ];
  vi.spyOn(operatorApiClient, "getProjectAIConfig").mockResolvedValue(config);
  vi.spyOn(operatorApiClient, "getProjectAIConfigHistory").mockResolvedValue([]);
  vi.spyOn(operatorApiClient, "getProviderCapabilities").mockResolvedValue([capability]);
  const create = vi.spyOn(operatorApiClient, "createProjectAIConfig").mockResolvedValue({
    id: "22222222-2222-2222-2222-222222222222",
    project_id: projectFixture.id,
    revision_number: 1,
    configuration_hash: "b".repeat(64),
    configuration: { execution: { profile_id: "custom" } },
    schema_version: 2,
    source: "project_revision",
    reason: "Keep Quality values",
    created_by: "test-admin",
    restored_from_revision_id: null,
    created_at: "2026-08-16T00:00:00Z",
  });

  renderOperatorComponent(
    <OperatorConsoleApp />,
    `/projects?project=${projectFixture.id}&section=ai-config`,
  );

  const profile = await screen.findByLabelText("RAG profile");
  await userEvent.selectOptions(profile, "standard");
  await userEvent.selectOptions(profile, "quality");
  await userEvent.selectOptions(profile, "custom");
  await userEvent.type(screen.getByLabelText("Revision reason"), "Keep Quality values");
  await userEvent.click(screen.getByRole("button", { name: "Create and activate revision" }));

  await waitFor(() => expect(create).toHaveBeenCalled());
  expect(create.mock.calls[0]?.[1].execution).toMatchObject({
    profile_id: "custom",
    retrieval_top_k: 12,
    semantic_candidate_top_k: 80,
    rerank_mode: "always",
  });
});

test("a conflicting stored value cannot make a preset custom", async () => {
  mockProjectShell();
  const revision: ProjectAIConfigRevision = {
    id: "22222222-2222-2222-2222-222222222222",
    project_id: projectFixture.id,
    revision_number: 1,
    configuration_hash: "b".repeat(64),
    configuration: {
      behavior: { translation_policy: "inherit" },
      execution: { profile_id: "standard", retrieval_top_k: 11 },
    },
    schema_version: 2,
    source: "project_revision",
    reason: "Custom top K",
    created_by: "test-admin",
    restored_from_revision_id: null,
    created_at: "2026-08-16T00:00:00Z",
  };
  const config = effectiveConfig(revision.id);
  config.base_profile_id = "standard";
  config.custom_execution = true;
  config.configuration = {
    ...config.configuration,
    retrieval: { ...config.configuration.retrieval, top_k: 11 },
  };
  config.rag_profiles = [
    {
      id: "standard",
      certification_status: "certified",
      selectable: true,
      recommended: true,
      profile_hash: "f".repeat(64),
      values: { retrieval_top_k: 10, rerank_mode: "always" },
    },
  ];
  vi.spyOn(operatorApiClient, "getProjectAIConfig").mockResolvedValue(config);
  vi.spyOn(operatorApiClient, "getProjectAIConfigHistory").mockResolvedValue([revision]);
  vi.spyOn(operatorApiClient, "getProviderCapabilities").mockResolvedValue([capability]);
  const create = vi.spyOn(operatorApiClient, "createProjectAIConfig").mockResolvedValue(revision);

  renderOperatorComponent(
    <OperatorConsoleApp />,
    `/projects?project=${projectFixture.id}&section=ai-config`,
  );

  expect(await screen.findByTestId("rag-profile-state")).toHaveTextContent("standard profile");
  fireEvent.change(screen.getByLabelText("Top K"), { target: { value: "10" } });
  expect(screen.getByTestId("rag-profile-state")).toHaveTextContent("Custom");
  await userEvent.selectOptions(screen.getByLabelText("RAG profile"), "standard");
  await userEvent.type(screen.getByLabelText("Revision reason"), "Restore exact Standard");
  await userEvent.click(screen.getByRole("button", { name: "Create and activate revision" }));

  await waitFor(() => expect(create).toHaveBeenCalled());
  expect(create.mock.calls[0]?.[1].execution).toEqual({ profile_id: "standard" });
});

test("reselecting a profile clears all hidden execution overrides", async () => {
  mockProjectShell();
  const revision: ProjectAIConfigRevision = {
    id: "22222222-2222-2222-2222-222222222222",
    project_id: projectFixture.id,
    revision_number: 1,
    configuration_hash: "b".repeat(64),
    configuration: {
      behavior: { translation_policy: "inherit" },
      execution: {
        profile_id: "standard",
        semantic_candidate_top_k: 55,
        passage_window_tokens: 120,
      },
    },
    schema_version: 2,
    source: "project_revision",
    reason: "Custom execution",
    created_by: "test-admin",
    restored_from_revision_id: null,
    created_at: "2026-08-16T00:00:00Z",
  };
  const config = effectiveConfig(revision.id);
  config.base_profile_id = "standard";
  config.custom_execution = true;
  config.rag_profiles = [
    {
      id: "standard",
      certification_status: "certified",
      selectable: true,
      recommended: true,
      profile_hash: "f".repeat(64),
      values: { retrieval_top_k: 10, rerank_mode: "always" },
    },
  ];
  vi.spyOn(operatorApiClient, "getProjectAIConfig").mockResolvedValue(config);
  vi.spyOn(operatorApiClient, "getProjectAIConfigHistory").mockResolvedValue([revision]);
  vi.spyOn(operatorApiClient, "getProviderCapabilities").mockResolvedValue([capability]);
  const create = vi.spyOn(operatorApiClient, "createProjectAIConfig").mockResolvedValue(revision);

  renderOperatorComponent(
    <OperatorConsoleApp />,
    `/projects?project=${projectFixture.id}&section=ai-config`,
  );

  expect(await screen.findByTestId("rag-profile-state")).toHaveTextContent("standard profile");
  fireEvent.change(screen.getByLabelText("RAG profile"), { target: { value: "standard" } });
  expect(screen.getByTestId("rag-profile-state")).toHaveTextContent("standard profile");
  await userEvent.type(screen.getByLabelText("Revision reason"), "Reset Standard controls");
  await userEvent.click(screen.getByRole("button", { name: "Create and activate revision" }));

  await waitFor(() => expect(create).toHaveBeenCalled());
  expect(create.mock.calls[0]?.[1].execution).toEqual({ profile_id: "standard" });
});

test("shows exact generation IDs and keeps candidate profiles selectable", async () => {
  mockProjectShell();
  const config = effectiveConfig(null);
  config.rag_profiles = (config.rag_profiles ?? []).map((profile) => ({
    ...profile,
    selectable: true,
  }));
  vi.spyOn(operatorApiClient, "getProjectAIConfig").mockResolvedValue(config);
  vi.spyOn(operatorApiClient, "getProjectAIConfigHistory").mockResolvedValue([]);
  vi.spyOn(operatorApiClient, "getProviderCapabilities").mockResolvedValue([capability]);

  renderOperatorComponent(
    <OperatorConsoleApp />,
    `/projects?project=${projectFixture.id}&section=ai-config`,
  );

  expect(await screen.findByRole("option", { name: /openai-o1-test/ })).toBeInTheDocument();
  expect(screen.getByRole("option", { name: /standard — candidate/ })).not.toBeDisabled();
  expect(screen.queryByLabelText("Provider")).not.toBeInTheDocument();
});

test("keeps inherited AI fields enabled and syncs the inherit switch", async () => {
  mockProjectShell();
  vi.spyOn(operatorApiClient, "getProjectAIConfig").mockResolvedValue({
    ...effectiveConfig(null),
    origins: {
      "retrieval.top_k": "project",
    },
  });
  vi.spyOn(operatorApiClient, "getProjectAIConfigHistory").mockResolvedValue([]);
  vi.spyOn(operatorApiClient, "getProviderCapabilities").mockResolvedValue([capability]);

  renderOperatorComponent(
    <OperatorConsoleApp />,
    `/projects?project=${projectFixture.id}&section=ai-config`,
  );

  const overrideTrigger = await screen.findByRole("button", { name: /1 Project override/ });
  expect(overrideTrigger).toBeInTheDocument();
  expect(screen.queryByRole("dialog", { name: "Project overrides" })).not.toBeInTheDocument();
  await userEvent.click(overrideTrigger);
  const overrideDialog = await screen.findByRole("dialog", { name: "Project overrides" });
  expect(within(overrideDialog).getByText("Top K")).toBeInTheDocument();
  expect(within(overrideDialog).getByText("Project")).toBeInTheDocument();
  expect(within(overrideDialog).queryByText(/evidence score/i)).not.toBeInTheDocument();
  await userEvent.click(within(overrideDialog).getByRole("button", { name: "Close" }));
  expect(screen.queryByRole("dialog", { name: "Project overrides" })).not.toBeInTheDocument();

  const model = screen.getByLabelText("Generation model");
  const inheritModel = screen.getByLabelText("Generation model: inherit global");
  expect(inheritModel).toBeChecked();
  expect(model).toHaveValue("openai-o1-test");
  await userEvent.selectOptions(model, "openai-gpt-4o-mini");
  expect(inheritModel).not.toBeChecked();
  await userEvent.selectOptions(model, "openai-o1-test");
  expect(inheritModel).toBeChecked();
  await userEvent.selectOptions(model, "openai-gpt-4o-mini");
  expect(inheritModel).not.toBeChecked();
  await userEvent.selectOptions(model, "");
  expect(inheritModel).toBeChecked();
  await userEvent.selectOptions(model, "openai-gpt-4o-mini");
  await userEvent.click(inheritModel);
  expect(inheritModel).toBeChecked();
  expect(model).toHaveValue("openai-o1-test");

  const responseMode = screen.getByLabelText("Response mode");
  const inheritResponse = screen.getByLabelText("Response mode: inherit global");
  expect(inheritResponse).toBeChecked();
  expect(responseMode).toHaveValue("indexed_only");
  await userEvent.selectOptions(responseMode, "indexed_then_web");
  expect(inheritResponse).not.toBeChecked();
  await userEvent.selectOptions(responseMode, "indexed_only");
  expect(inheritResponse).toBeChecked();

  const grounding = screen.getByLabelText("Grounding assurance");
  const inheritGrounding = screen.getByLabelText("Grounding assurance: inherit global");
  expect(inheritGrounding).toBeChecked();
  await userEvent.selectOptions(grounding, "balanced");
  expect(inheritGrounding).not.toBeChecked();
  await userEvent.selectOptions(grounding, "strict");
  expect(inheritGrounding).toBeChecked();
  await userEvent.selectOptions(grounding, "balanced");
  await userEvent.click(inheritGrounding);
  expect(inheritGrounding).toBeChecked();
  expect(grounding).toHaveValue("inherit");

  const domain = screen.getByLabelText("Domain instructions");
  const inheritDomain = screen.getByLabelText("Domain instructions: inherit global");
  expect(inheritDomain).toBeChecked();
  await userEvent.type(domain, "Project-specific tax instructions");
  expect(inheritDomain).not.toBeChecked();
  await userEvent.clear(domain);
  expect(inheritDomain).toBeChecked();

  const translation = screen.getByLabelText("Query translation");
  expect(translation).toHaveValue("inherit");
  await userEvent.selectOptions(translation, "enabled");
  expect(translation).toHaveValue("enabled");
});

test("restores deployment values when Inherit is turned back on for an overridden Project", async () => {
  mockProjectShell();
  const revision: ProjectAIConfigRevision = {
    id: "22222222-2222-2222-2222-222222222222",
    project_id: projectFixture.id,
    revision_number: 1,
    configuration_hash: "b".repeat(64),
    configuration: {
      behavior: {
        generation_model_id: "openai-gpt-4o-mini",
        response_mode: "indexed_then_web",
        grounding_assurance: "balanced",
        domain_instructions: "Use only this Project's tax notes.",
      },
    },
    schema_version: 2,
    source: "project_revision",
    reason: "Project-specific AI settings",
    created_by: "test-admin",
    restored_from_revision_id: null,
    created_at: "2026-08-16T00:00:00Z",
  };
  const config = effectiveConfig(revision.id);
  config.configuration = {
    ...config.configuration,
    llm: { ...config.configuration.llm, generation_model_id: "openai-gpt-4o-mini" },
    chat: {
      ...config.configuration.chat,
      response_mode: "indexed_then_web",
      grounding_mode: "balanced",
    },
    domain_instructions: "Use only this Project's tax notes.",
  };
  vi.spyOn(operatorApiClient, "getProjectAIConfig").mockResolvedValue(config);
  vi.spyOn(operatorApiClient, "getProjectAIConfigHistory").mockResolvedValue([revision]);
  vi.spyOn(operatorApiClient, "getProviderCapabilities").mockResolvedValue([capability]);

  renderOperatorComponent(
    <OperatorConsoleApp />,
    `/projects?project=${projectFixture.id}&section=ai-config`,
  );

  const model = await screen.findByLabelText("Generation model");
  const inheritModel = screen.getByLabelText("Generation model: inherit global");
  expect(model).toHaveValue("openai-gpt-4o-mini");
  expect(inheritModel).not.toBeChecked();
  await userEvent.selectOptions(model, "openai-o1-test");
  expect(inheritModel).toBeChecked();
  await userEvent.selectOptions(model, "openai-gpt-4o-mini");
  expect(inheritModel).not.toBeChecked();
  await userEvent.click(inheritModel);
  expect(inheritModel).toBeChecked();
  expect(model).toHaveValue("openai-o1-test");

  const responseMode = screen.getByLabelText("Response mode");
  const inheritResponse = screen.getByLabelText("Response mode: inherit global");
  expect(responseMode).toHaveValue("indexed_then_web");
  expect(inheritResponse).not.toBeChecked();
  await userEvent.selectOptions(responseMode, "indexed_only");
  expect(inheritResponse).toBeChecked();
  await userEvent.selectOptions(responseMode, "indexed_then_web");
  await userEvent.click(inheritResponse);
  expect(inheritResponse).toBeChecked();
  expect(responseMode).toHaveValue("indexed_only");

  const grounding = screen.getByLabelText("Grounding assurance");
  const inheritGrounding = screen.getByLabelText("Grounding assurance: inherit global");
  expect(grounding).toHaveValue("balanced");
  expect(inheritGrounding).not.toBeChecked();
  await userEvent.click(inheritGrounding);
  expect(inheritGrounding).toBeChecked();
  expect(grounding).toHaveValue("inherit");

  const domain = screen.getByLabelText("Domain instructions");
  const inheritDomain = screen.getByLabelText("Domain instructions: inherit global");
  expect(domain).toHaveValue("Use only this Project's tax notes.");
  expect(inheritDomain).not.toBeChecked();
  await userEvent.click(inheritDomain);
  expect(inheritDomain).toBeChecked();
  expect(domain).toHaveValue("");
});

test("restores the deployment response mode in Create Project when Inherit is turned back on", async () => {
  mockProjectShell();

  renderOperatorComponent(<OperatorConsoleApp />, `/projects?project=${projectFixture.id}`);
  await userEvent.click(await screen.findByRole("button", { name: "Create Project" }));
  await userEvent.click(screen.getByText("Optional AI settings"));

  const responseMode = await screen.findByLabelText("Response mode");
  const inheritMode = screen.getByLabelText("Response mode: inherit global");
  expect(inheritMode).toBeChecked();
  await userEvent.selectOptions(responseMode, "indexed_then_web");
  expect(inheritMode).not.toBeChecked();
  await userEvent.click(inheritMode);
  expect(inheritMode).toBeChecked();
  expect(responseMode).toHaveValue("indexed_only");
});

test("uploads a document into an existing source group or a modifying group", async () => {
  mockProjectShell();
  const document: Document = {
    id: "33333333-3333-3333-3333-333333333333",
    project_id: projectFixture.id,
    filename: "existing.pdf",
    content_type: "application/pdf",
    size_bytes: 100,
    storage_key: "raw/existing.pdf",
    content_sha256: "d".repeat(64),
    status: "ready",
    version: 1,
    deleted_at: null,
    created_at: "2026-08-16T00:00:00Z",
    updated_at: "2026-08-16T00:00:00Z",
  };
  const sourceState: SourceState = {
    project_id: projectFixture.id,
    generation: 1,
    current_generation: 1,
    items: [
      {
        document_id: document.id,
        activation: {
          id: "44444444-4444-4444-4444-444444444444",
          project_id: projectFixture.id,
          document_id: document.id,
          source_revision_id: "55555555-5555-5555-5555-555555555555",
          generation: 1,
          activated_by: "test-admin",
          reason: "Initial",
          created_at: "2026-08-16T00:00:00Z",
        },
        revision: {
          id: "55555555-5555-5555-5555-555555555555",
          project_id: projectFixture.id,
          document_id: document.id,
          source_group_id: "66666666-6666-6666-6666-666666666666",
          revision_number: 1,
          revision_label: "Initial",
          title: "Existing source",
          source_type: null,
          published_date: "2023-07-01",
          effective_from: "2023-07-01",
          effective_to: "2024-06-30",
          lifecycle_status: "active",
          source_role: "primary",
          change_reason: "Initial",
          created_by: "test-admin",
          content_hash: "d".repeat(64),
          created_at: "2026-08-16T00:00:00Z",
          relationships: [],
          warnings: [],
        },
      },
    ],
  };
  const sourceItem = sourceState.items[0];
  if (!sourceItem) {
    throw new Error("Expected an existing source fixture");
  }
  vi.spyOn(operatorApiClient, "getDocuments").mockResolvedValue({
    items: [document],
    total: 1,
    limit: 100,
    offset: 0,
  });
  vi.spyOn(operatorApiClient, "getSourceState").mockResolvedValue(sourceState);
  vi.spyOn(operatorApiClient, "getSourceRevisions").mockResolvedValue([sourceItem.revision]);
  vi.spyOn(operatorApiClient, "getSourceActivations").mockResolvedValue([sourceItem.activation]);
  const upload = vi.spyOn(operatorApiClient, "uploadDocument").mockResolvedValue(document);
  const createRevision = vi.spyOn(operatorApiClient, "createSourceRevision").mockResolvedValue({
    revision: sourceItem.revision,
    activation: sourceItem.activation,
  });

  renderOperatorComponent(
    <OperatorConsoleApp />,
    `/projects?project=${projectFixture.id}&section=sources`,
  );

  const target = sourceItem.revision;
  await screen.findByText("Upload document and optional source metadata");
  expect(screen.getAllByLabelText<HTMLInputElement>("Published")[1]).toHaveValue("2023-07-01");
  expect(screen.getAllByLabelText<HTMLInputElement>("Effective from")[1]).toHaveValue("2023-07-01");
  expect(screen.getAllByLabelText<HTMLInputElement>("Effective to")[1]).toHaveValue("2024-06-30");
  fireEvent.submit(
    screen.getByRole("button", { name: "Save metadata correction" }).closest("form")!,
  );
  await waitFor(() => expect(createRevision).toHaveBeenCalledTimes(1));
  expect(createRevision.mock.calls[0]?.[2]).toMatchObject({
    published_date: "2023-07-01",
    effective_from: "2023-07-01",
    effective_to: "2024-06-30",
  });
  expect(createRevision.mock.calls[0]?.[2]).not.toHaveProperty("change_reason");
  await userEvent.upload(
    screen.getByLabelText("File"),
    new File(["revision"], "revision.pdf", { type: "application/pdf" }),
  );
  await userEvent.selectOptions(screen.getByLabelText("Source treatment"), "revision");
  await userEvent.selectOptions(screen.getByLabelText("Existing source revision"), target.id);
  expect(screen.getByLabelText<HTMLInputElement>("File").files).toHaveLength(1);
  expect(screen.getByRole("button", { name: "Upload" })).toBeEnabled();
  fireEvent.submit(screen.getByRole("button", { name: "Upload" }).closest("form")!);

  await waitFor(() => expect(upload).toHaveBeenCalledTimes(1));
  const firstUploadCall = upload.mock.calls[0];
  if (!firstUploadCall) {
    throw new Error("Expected the revision upload to be submitted");
  }
  expect(firstUploadCall[3]).toMatchObject({
    create_new_group: false,
    source_group_id: target.source_group_id,
    relationships: [{ relationship_type: "replaces", target_revision_id: target.id }],
  });

  await userEvent.upload(
    screen.getByLabelText("File"),
    new File(["modifier"], "modifier.pdf", { type: "application/pdf" }),
  );
  await userEvent.selectOptions(screen.getByLabelText("Source treatment"), "modifies");
  await userEvent.selectOptions(screen.getByLabelText("Existing source revision"), target.id);
  fireEvent.submit(screen.getByRole("button", { name: "Upload" }).closest("form")!);

  await waitFor(() => expect(upload).toHaveBeenCalledTimes(2));
  const secondUploadCall = upload.mock.calls[1];
  if (!secondUploadCall) {
    throw new Error("Expected the modifying upload to be submitted");
  }
  expect(secondUploadCall[3]).toMatchObject({
    create_new_group: true,
    relationships: [{ relationship_type: "modifies", target_revision_id: target.id }],
  });
  expect(secondUploadCall[3]).not.toHaveProperty("source_group_id");

  await userEvent.upload(
    screen.getByLabelText("File"),
    new File(["independent"], "independent.pdf", { type: "application/pdf" }),
  );
  await userEvent.type(
    screen.getByLabelText("Source title (optional quick active defaults)"),
    "Independent source",
  );
  fireEvent.submit(screen.getByRole("button", { name: "Upload" }).closest("form")!);

  await waitFor(() => expect(upload).toHaveBeenCalledTimes(3));
  const thirdUploadCall = upload.mock.calls[2];
  if (!thirdUploadCall) {
    throw new Error("Expected the independent upload to be submitted");
  }
  expect(thirdUploadCall[3]).toMatchObject({
    create_new_group: true,
    relationships: [],
    title: "Independent source",
  });
});
