import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import {
  OperatorApiError,
  operatorApiClient,
  type Document,
  type IndexBuild,
  type Job,
  type Message,
} from "../../api/operatorApiClient";
import { OperatorConsoleApp } from "../../app/OperatorConsoleApp";
import { renderOperatorComponent } from "../../test/renderOperatorComponent";
import { jobFixture, now, projectFixture } from "../../test/operatorTestFixtures";

const documentFixture: Document = {
  id: "33333333-3333-3333-3333-333333333333",
  project_id: projectFixture.id,
  filename: "policy.txt",
  content_type: "text/plain",
  size_bytes: 120,
  storage_key: "raw/policy.txt",
  content_sha256: "a".repeat(64),
  status: "ready",
  version: 1,
  error_message: null,
  parser_name: "text",
  parser_version: "1",
  accepted_parser: "text",
  parse_quality_score: 1,
  extraction_method: "native",
  page_count: 1,
  language: "en",
  ocr_lang: null,
  parsed_text_storage_key: "parsed/policy.txt",
  deleted_at: null,
  created_at: now,
  updated_at: now,
  job_id: jobFixture.id,
};

const buildFixture: IndexBuild = {
  id: "88888888-8888-8888-8888-888888888888",
  project_id: projectFixture.id,
  job_id: jobFixture.id,
  state: "active",
  operation: "reindex",
  embedding_set_version: 1,
  configuration_hash: "b".repeat(64),
  corpus_fingerprint: "c".repeat(64),
  document_count: 1,
  chunk_count: 2,
  vector_count: 2,
  keyword_count: 2,
  manifest: {},
  validated_at: now,
  activated_at: now,
  failure_code: null,
  failure_message: null,
  created_at: now,
  updated_at: now,
};

const succeededJob: Job = {
  ...jobFixture,
  state: "succeeded",
  stage: "completed",
  progress: 100,
  failure_code: null,
  failure_message: null,
};

function mockLabBase({
  documents = [documentFixture],
  jobs = [succeededJob],
}: { documents?: Document[]; jobs?: Job[] } = {}) {
  vi.spyOn(operatorApiClient, "getProjects").mockResolvedValue({
    items: [projectFixture],
    total: 1,
    limit: 100,
    offset: 0,
  });
  vi.spyOn(operatorApiClient, "getDocuments").mockResolvedValue({
    items: documents,
    total: documents.length,
    limit: 100,
    offset: 0,
  });
  vi.spyOn(operatorApiClient, "getJobs").mockResolvedValue({
    items: jobs,
    total: jobs.length,
    limit: 100,
    offset: 0,
  });
  vi.spyOn(operatorApiClient, "getIndexBuilds").mockResolvedValue({
    items: [buildFixture],
    active_build_id: buildFixture.id,
    previous_build_id: null,
  });
}

test("routes to Test Lab, keeps project selection, and derives Journey progress from backend state", async () => {
  mockLabBase();
  renderOperatorComponent(<OperatorConsoleApp />, `/lab?project=${projectFixture.id}&tab=journey`);
  expect(await screen.findByRole("heading", { name: "Test Lab", level: 1 })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Test Lab" })).toHaveClass("nav-link--active");
  expect(
    await screen.findByText(/policy.txt is ready/, {}, { timeout: 5_000 }),
  ).toBeInTheDocument();
  expect(screen.getByRole("combobox", { name: "Project" })).toHaveValue(projectFixture.id);
  expect(screen.getByText(/Active build is 88888888/)).toBeInTheDocument();
});

test("distinguishes an accepted upload from terminal processing success", async () => {
  mockLabBase();
  vi.spyOn(operatorApiClient, "uploadDocument").mockResolvedValue({
    ...documentFixture,
    status: "queued",
  });
  renderOperatorComponent(
    <OperatorConsoleApp />,
    `/lab?project=${projectFixture.id}&tab=documents`,
  );
  const picker = (await screen.findByText("Drop a document here")).closest("label")!;
  const fileInput = picker.querySelector("input[type=file]") as HTMLInputElement;
  await userEvent.upload(
    fileInput,
    new File(["refund policy"], "policy.txt", { type: "text/plain" }),
  );
  expect(await screen.findByText("Request accepted")).toBeInTheDocument();
  expect(screen.getByText("Processing finished successfully")).toBeInTheDocument();
  expect(
    screen
      .getAllByRole("link", { name: jobFixture.id })
      .some(
        (link) =>
          link.getAttribute("href") === `/jobs?project=${projectFixture.id}&job=${jobFixture.id}`,
      ),
  ).toBe(true);
});

test("marks expected search words pass and exposes active build and result metadata", async () => {
  mockLabBase();
  vi.spyOn(operatorApiClient, "search").mockResolvedValue({
    query: "refund",
    top_k: 5,
    diagnostics: {
      strategy: "hybrid",
      duration_ms: 14,
      rerank_requested: false,
      rerank_status: "not_requested",
    },
    results: [
      {
        chunk_id: "77777777-7777-7777-7777-777777777777",
        document_id: documentFixture.id,
        chunk_index: 0,
        content: "Refund requests are accepted within thirty days.",
        score: 0.91,
        filename: documentFixture.filename,
        page_number: 1,
        char_start: 0,
        char_end: 51,
        metadata: {},
      },
    ],
  });
  renderOperatorComponent(<OperatorConsoleApp />, `/lab?project=${projectFixture.id}&tab=search`);
  await userEvent.type(await screen.findByLabelText("Query"), "refund");
  await userEvent.type(screen.getByLabelText("Expected words (optional)"), "thirty days");
  await userEvent.click(screen.getAllByRole("button", { name: "Search" }).at(-1)!);
  expect(await screen.findByText(/Expected words “thirty days” were found/)).toBeInTheDocument();
  expect(screen.getByText(/active build 88888888/)).toBeInTheDocument();
  expect(screen.getByText(/Page 1 · chunk 0 · score 0.9100/)).toBeInTheDocument();
});

test("marks expected search words as needs attention when no returned chunk contains them", async () => {
  mockLabBase();
  vi.spyOn(operatorApiClient, "search").mockResolvedValue({
    query: "refund",
    top_k: 5,
    diagnostics: {
      strategy: "hybrid",
      duration_ms: 8,
      rerank_requested: false,
      rerank_status: "not_requested",
    },
    results: [
      {
        chunk_id: "77777777-7777-7777-7777-777777777777",
        document_id: documentFixture.id,
        chunk_index: 0,
        content: "A returned chunk without the assertion phrase.",
        score: 0.5,
        filename: documentFixture.filename,
        page_number: null,
        char_start: null,
        char_end: null,
        metadata: {},
      },
    ],
  });
  renderOperatorComponent(<OperatorConsoleApp />, `/lab?project=${projectFixture.id}&tab=search`);
  await userEvent.type(await screen.findByLabelText("Query"), "refund");
  await userEvent.type(screen.getByLabelText("Expected words (optional)"), "thirty days");
  await userEvent.click(screen.getAllByRole("button", { name: "Search" }).at(-1)!);
  expect(
    await screen.findByText(/Expected words “thirty days” were not found/),
  ).toBeInTheDocument();
  expect(screen.getByText("needs attention")).toBeInTheDocument();
});

test("renders grounded citations instead of inferring grounding from answer text", async () => {
  mockLabBase();
  const conversationId = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
  vi.spyOn(operatorApiClient, "createConversation").mockResolvedValue({
    id: conversationId,
    project_id: projectFixture.id,
    title: "Test Lab",
    provider: null,
    model: null,
    temperature: null,
    system_prompt_version: null,
    last_message_at: null,
    is_active: true,
    deleted_at: null,
    deleted_by: null,
    created_at: now,
    updated_at: now,
  });
  vi.spyOn(operatorApiClient, "getMessages").mockResolvedValue({
    items: [],
    total: 0,
    limit: 200,
    offset: 0,
  });
  const userMessage: Message = {
    id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    project_id: projectFixture.id,
    conversation_id: conversationId,
    role: "user",
    content: "What is the policy?",
    finish_reason: null,
    input_tokens: null,
    output_tokens: null,
    prompt_version: null,
    embedding_set_version: null,
    provider: null,
    model: null,
    metadata: {},
    citations: [],
    claims: [],
    grounded: null,
    insufficient_evidence_reason: null,
    created_at: now,
    updated_at: now,
  };
  vi.spyOn(operatorApiClient, "sendMessage").mockResolvedValue({
    user_message: userMessage,
    assistant_message: {
      ...userMessage,
      id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
      role: "assistant",
      content: "Refunds are accepted within thirty days.",
      grounded: true,
      citations: [
        {
          chunk_id: "77777777-7777-7777-7777-777777777777",
          document_id: documentFixture.id,
          filename: documentFixture.filename,
          chunk_index: 0,
          page_number: 1,
          char_start: 0,
          char_end: 51,
          score: 0.91,
          chunk_hash: "d".repeat(64),
          excerpt: "Refund requests are accepted within thirty days.",
        },
      ],
    },
  });
  renderOperatorComponent(<OperatorConsoleApp />, `/lab?project=${projectFixture.id}&tab=messages`);
  await userEvent.click(
    (await screen.findAllByRole("button", { name: "New test conversation" })).at(-1)!,
  );
  await userEvent.type(await screen.findByLabelText("Message"), "What is the policy?");
  await userEvent.click(screen.getByRole("button", { name: "Send message" }));
  expect(await screen.findByText("Answer with citations")).toBeInTheDocument();
  expect(screen.getByText("[1] policy.txt")).toBeInTheDocument();
  expect(screen.getByText(/Refund requests are accepted/)).toBeInTheDocument();
});

test("renders an explicit valid refusal when retrieval evidence is insufficient", async () => {
  mockLabBase();
  const conversationId = "dddddddd-dddd-dddd-dddd-dddddddddddd";
  vi.spyOn(operatorApiClient, "createConversation").mockResolvedValue({
    id: conversationId,
    project_id: projectFixture.id,
    title: "Refusal test",
    provider: null,
    model: null,
    temperature: null,
    system_prompt_version: null,
    last_message_at: null,
    is_active: true,
    deleted_at: null,
    deleted_by: null,
    created_at: now,
    updated_at: now,
  });
  vi.spyOn(operatorApiClient, "getMessages").mockResolvedValue({
    items: [],
    total: 0,
    limit: 200,
    offset: 0,
  });
  const userMessage: Message = {
    id: "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
    project_id: projectFixture.id,
    conversation_id: conversationId,
    role: "user",
    content: "What is not in the corpus?",
    finish_reason: null,
    input_tokens: null,
    output_tokens: null,
    prompt_version: null,
    embedding_set_version: null,
    provider: null,
    model: null,
    metadata: {},
    citations: [],
    claims: [],
    grounded: null,
    insufficient_evidence_reason: null,
    created_at: now,
    updated_at: now,
  };
  vi.spyOn(operatorApiClient, "sendMessage").mockResolvedValue({
    user_message: userMessage,
    assistant_message: {
      ...userMessage,
      id: "ffffffff-ffff-ffff-ffff-ffffffffffff",
      role: "assistant",
      content: "I do not have enough evidence to answer.",
      finish_reason: "insufficient_evidence",
      grounded: false,
      insufficient_evidence_reason: "no_retrieval_results",
    },
  });
  renderOperatorComponent(<OperatorConsoleApp />, `/lab?project=${projectFixture.id}&tab=messages`);
  await userEvent.click(
    (await screen.findAllByRole("button", { name: "New test conversation" })).at(-1)!,
  );
  await userEvent.type(await screen.findByLabelText("Message"), "What is not in the corpus?");
  await userEvent.click(screen.getByRole("button", { name: "Send message" }));
  expect(await screen.findByText("Valid refusal / insufficient evidence")).toBeInTheDocument();
  expect(screen.getByText("no retrieval results")).toBeInTheDocument();
});

test("requires the exact filename before enabling irreversible purge and shows API trace facts", async () => {
  mockLabBase();
  renderOperatorComponent(
    <OperatorConsoleApp />,
    `/lab?project=${projectFixture.id}&tab=documents`,
  );
  const purge = await screen.findByRole("button", { name: "Purge" });
  expect(purge).toBeDisabled();
  await userEvent.type(screen.getByLabelText("Purge confirmation"), "wrong.txt");
  expect(purge).toBeDisabled();
  await userEvent.clear(screen.getByLabelText("Purge confirmation"));
  await userEvent.type(screen.getByLabelText("Purge confirmation"), documentFixture.filename);
  expect(purge).toBeEnabled();

  vi.spyOn(operatorApiClient, "purgeDocument").mockRejectedValue(
    new OperatorApiError("Purge was rejected.", 409, "document_purge_conflict", "trace-123"),
  );
  await userEvent.click(purge);
  const alert = await screen.findByRole("alert");
  expect(within(alert).getByText("Code: document_purge_conflict")).toBeInTheDocument();
  expect(within(alert).getByText("Trace ID: trace-123")).toBeInTheDocument();
});
