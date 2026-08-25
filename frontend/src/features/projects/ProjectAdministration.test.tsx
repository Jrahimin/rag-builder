import { fireEvent, screen, waitFor } from "@testing-library/react";
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
  return {
    project_id: projectFixture.id,
    active_revision_id: activeRevisionId,
    configuration_hash: "a".repeat(64),
    configuration: {
      llm: { provider: "openai", model: "o1-test", temperature: null, max_tokens: 2048 },
      retrieval: {
        strategy: "hybrid",
        top_k: 10,
        rerank_enabled: true,
        rerank_mode: "always",
        rerank_top_n: 20,
        rerank_candidate_window: 25,
        rerank_return_n: 8,
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
        max_context_chunks: 8,
        context_char_budget: 12_000,
        max_history_messages: 10,
        include_citations: true,
        citation_excerpt_max_chars: 500,
        evidence_gate_mode: "enforce",
        evidence_score_mode: "whole_chunk",
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
      source_policy_mode: "off",
    },
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
  };
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

test("keeps inherited AI values sparse and can clear a Project override", async () => {
  mockProjectShell();
  const revision: ProjectAIConfigRevision = {
    id: "22222222-2222-2222-2222-222222222222",
    project_id: projectFixture.id,
    revision_number: 1,
    configuration_hash: "b".repeat(64),
    configuration: {
      retrieval: { top_k: 23, rerank_top_n: 42 },
      chat: { context_char_budget: 20_000 },
    },
    reason: "Initial override",
    created_by: "test-admin",
    restored_from_revision_id: null,
    created_at: "2026-08-16T00:00:00Z",
  };
  vi.spyOn(operatorApiClient, "getProjectAIConfig").mockResolvedValue(effectiveConfig(revision.id));
  vi.spyOn(operatorApiClient, "getProjectAIConfigHistory").mockResolvedValue([revision]);
  vi.spyOn(operatorApiClient, "getProviderCapabilities").mockResolvedValue([capability]);
  const create = vi.spyOn(operatorApiClient, "createProjectAIConfig").mockResolvedValue(revision);

  renderOperatorComponent(
    <OperatorConsoleApp />,
    `/projects?project=${projectFixture.id}&section=ai-config`,
  );

  const inheritTopK = await screen.findByLabelText("Top K: inherit global");
  expect(inheritTopK).not.toBeChecked();
  await userEvent.click(inheritTopK);
  await userEvent.type(screen.getByLabelText("Revision reason"), "Return top K to global");
  await userEvent.click(screen.getByRole("button", { name: "Create and activate revision" }));

  await waitFor(() => expect(create).toHaveBeenCalled());
  const firstCreateCall = create.mock.calls[0];
  if (!firstCreateCall) {
    throw new Error("Expected a project AI-config revision to be created");
  }
  const saved = firstCreateCall[1];
  expect(saved.retrieval).toEqual({ rerank_top_n: 42 });
  expect(saved.chat).toEqual({ context_char_budget: 20_000 });
  expect(saved.llm).toEqual({});
  expect(saved).not.toHaveProperty("domain_instructions");
  expect(saved).not.toHaveProperty("source_policy_mode");
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
      retrieval: { query_translation_enabled: true, rerank_mode: "cross_language" },
    },
    reason: "Enable multilingual retrieval",
    created_by: "test-admin",
    restored_from_revision_id: null,
    created_at: "2026-08-16T00:00:00Z",
  });

  renderOperatorComponent(
    <OperatorConsoleApp />,
    `/projects?project=${projectFixture.id}&section=ai-config`,
  );

  await userEvent.selectOptions(await screen.findByLabelText("Query translation"), "on");
  await userEvent.selectOptions(screen.getByLabelText("Rerank mode"), "cross_language");
  await userEvent.type(screen.getByLabelText("Revision reason"), "Enable multilingual retrieval");
  await userEvent.click(screen.getByRole("button", { name: "Create and activate revision" }));

  await waitFor(() => expect(create).toHaveBeenCalled());
  const saved = create.mock.calls[0]?.[1];
  expect(saved?.retrieval).toMatchObject({
    query_translation_enabled: true,
    rerank_mode: "cross_language",
  });
  expect(saved?.retrieval).not.toHaveProperty("rerank_enabled");
});

test("saves bounded web-search settings as sparse Project overrides", async () => {
  mockProjectShell();
  vi.spyOn(operatorApiClient, "getProjectAIConfig").mockResolvedValue(effectiveConfig(null));
  vi.spyOn(operatorApiClient, "getProjectAIConfigHistory").mockResolvedValue([]);
  vi.spyOn(operatorApiClient, "getProviderCapabilities").mockResolvedValue([capability]);
  const create = vi.spyOn(operatorApiClient, "createProjectAIConfig").mockResolvedValue({
    id: "22222222-2222-2222-2222-222222222222",
    project_id: projectFixture.id,
    revision_number: 1,
    configuration_hash: "b".repeat(64),
    configuration: { web_search: { enabled: true, max_results: 4 } },
    reason: "Limit web sources",
    created_by: "test-admin",
    restored_from_revision_id: null,
    created_at: "2026-08-16T00:00:00Z",
  });

  renderOperatorComponent(
    <OperatorConsoleApp />,
    `/projects?project=${projectFixture.id}&section=ai-config`,
  );

  await userEvent.click(await screen.findByLabelText("Web search: inherit global"));
  await userEvent.clear(screen.getByLabelText("Web results"));
  await userEvent.type(screen.getByLabelText("Web results"), "4");
  await userEvent.type(screen.getByLabelText("Revision reason"), "Limit web sources");
  await userEvent.click(screen.getByRole("button", { name: "Create and activate revision" }));

  await waitFor(() => expect(create).toHaveBeenCalled());
  expect(create.mock.calls[0]?.[1].web_search).toEqual({ enabled: true, max_results: 4 });
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
  await userEvent.selectOptions(screen.getByLabelText("Rerank mode"), "off");
  await userEvent.click(screen.getByRole("button", { name: "Create" }));

  await waitFor(() => expect(createProject).toHaveBeenCalled());
  await waitFor(() => expect(createConfig).toHaveBeenCalled());
  expect(
    await screen.findByText(/Project created. AI settings were not saved/i),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Open AI configuration" })).toBeInTheDocument();
});

test("uses provider capability rules for generation fields", async () => {
  mockProjectShell();
  vi.spyOn(operatorApiClient, "getProjectAIConfig").mockResolvedValue(effectiveConfig(null));
  vi.spyOn(operatorApiClient, "getProjectAIConfigHistory").mockResolvedValue([]);
  vi.spyOn(operatorApiClient, "getProviderCapabilities").mockResolvedValue([capability]);

  renderOperatorComponent(
    <OperatorConsoleApp />,
    `/projects?project=${projectFixture.id}&section=ai-config`,
  );

  expect(
    await screen.findByText("This provider/model does not support temperature."),
  ).toBeInTheDocument();
  expect(screen.getByLabelText("Temperature: inherit global")).toBeDisabled();
  await userEvent.click(screen.getByLabelText("Maximum output tokens: inherit global"));
  const maxTokens = screen.getByRole("spinbutton", { name: "Maximum output tokens" });
  expect(maxTokens).toHaveAttribute("min", "1");
  expect(maxTokens).toHaveAttribute("max", "128000");
});

test("keeps inherited AI fields enabled and syncs the inherit switch", async () => {
  mockProjectShell();
  vi.spyOn(operatorApiClient, "getProjectAIConfig").mockResolvedValue({
    ...effectiveConfig(null),
    origins: {
      "llm.provider": "global",
      "llm.model": "global",
      "retrieval.semantic_evidence_score_threshold": "global",
      "retrieval.top_k": "project",
    },
  });
  vi.spyOn(operatorApiClient, "getProjectAIConfigHistory").mockResolvedValue([]);
  vi.spyOn(operatorApiClient, "getProviderCapabilities").mockResolvedValue([capability]);

  renderOperatorComponent(
    <OperatorConsoleApp />,
    `/projects?project=${projectFixture.id}&section=ai-config`,
  );

  expect(await screen.findByText("Top K · Project")).toBeInTheDocument();
  expect(screen.queryByText(/evidence score/i)).not.toBeInTheDocument();

  const inheritProvider = screen.getByLabelText("Provider: inherit global");
  const provider = screen.getByLabelText("Provider");
  expect(inheritProvider).toBeChecked();
  expect(inheritProvider).toBeEnabled();
  expect(provider).toBeEnabled();

  await userEvent.selectOptions(provider, "ollama");
  expect(inheritProvider).not.toBeChecked();
  expect(provider).toHaveValue("ollama");

  await userEvent.click(inheritProvider);
  expect(inheritProvider).toBeChecked();
  expect(provider).toHaveValue("openai");

  const inheritTranslation = screen.getByLabelText("Query translation: inherit global");
  expect(inheritTranslation).toBeChecked();
  await userEvent.selectOptions(screen.getByLabelText("Query translation"), "on");
  expect(inheritTranslation).not.toBeChecked();
  await userEvent.click(inheritTranslation);
  expect(inheritTranslation).toBeChecked();
  expect(screen.getByLabelText("Query translation")).toHaveValue("inherit");
});

test("restores the deployment default in Create Project when Inherit is turned back on", async () => {
  mockProjectShell();

  renderOperatorComponent(<OperatorConsoleApp />, `/projects?project=${projectFixture.id}`);
  await userEvent.click(await screen.findByRole("button", { name: "Create Project" }));
  await userEvent.click(screen.getByText("Optional AI settings"));

  const provider = await screen.findByLabelText("Provider");
  const inheritProvider = screen.getByLabelText("Provider: inherit global");
  await waitFor(() => expect(provider).toHaveValue("openai"));
  expect(inheritProvider).toBeChecked();

  await userEvent.selectOptions(provider, "openai");
  expect(inheritProvider).toBeChecked();

  await userEvent.selectOptions(provider, "ollama");
  expect(inheritProvider).not.toBeChecked();
  expect(provider).toHaveValue("ollama");

  await userEvent.click(inheritProvider);
  expect(inheritProvider).toBeChecked();
  expect(provider).toHaveValue("openai");
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
          published_date: null,
          effective_from: null,
          effective_to: null,
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

  renderOperatorComponent(
    <OperatorConsoleApp />,
    `/projects?project=${projectFixture.id}&section=sources`,
  );

  const target = sourceItem.revision;
  await screen.findByText("Upload document and optional source metadata");
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
