import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, test, expect, vi } from "vitest";
import {
  operatorApiClient,
  type SourceRevision,
  type SourceState,
  type Document,
} from "../../api/operatorApiClient";
import { OperatorConsoleApp } from "../../app/OperatorConsoleApp";
import { renderOperatorComponent } from "../../test/renderOperatorComponent";
import { projectFixture, configurationFixture, now } from "../../test/operatorTestFixtures";

const base: SourceRevision = {
  id: "source-bn",
  project_id: projectFixture.id,
  document_id: "doc-bn",
  source_group_id: "history-bn",
  revision_number: 1,
  revision_label: "Initial",
  title: "Act Bangla",
  source_type: "act",
  published_date: null,
  effective_from: "2023-06-22",
  effective_to: null,
  lifecycle_status: "active",
  source_role: "primary",
  change_reason: "Initial",
  created_by: "test",
  content_hash: "a".repeat(64),
  created_at: now,
  relationships: [],
  warnings: [],
};
const english: SourceRevision = {
  ...base,
  id: "source-en",
  document_id: "doc-en",
  source_group_id: "history-en",
  title: "Act English",
};
const finance: SourceRevision = {
  ...base,
  id: "source-finance",
  document_id: "doc-finance",
  source_group_id: "history-finance",
  title: "Finance Act",
};

function setup() {
  vi.spyOn(operatorApiClient, "getAllOperatorProjects").mockResolvedValue({
    items: [projectFixture],
    total: 1,
    limit: 100,
    offset: 0,
  });
  const documents = [finance, base, english].map(
    (revision) =>
      ({
        id: revision.document_id,
        project_id: projectFixture.id,
        filename: revision.title + ".txt",
        status: "ready",
        version: 1,
        content_type: "text/plain",
        size_bytes: 10,
        created_at: now,
        updated_at: now,
      }) as Document,
  );
  const state: SourceState = {
    project_id: projectFixture.id,
    generation: 3,
    current_generation: 3,
    items: [finance, base, english].map((revision, i) => ({
      document_id: revision.document_id,
      revision,
      activation: {
        id: `activation-${i}`,
        document_id: revision.document_id,
        project_id: projectFixture.id,
        source_revision_id: revision.id,
        generation: i + 1,
        activated_by: "test",
        reason: "Initial",
        created_at: now,
      },
    })),
  };
  vi.spyOn(operatorApiClient, "getProjects").mockResolvedValue({
    items: [projectFixture],
    total: 1,
    limit: 100,
    offset: 0,
  });
  vi.spyOn(operatorApiClient, "getOrganizations").mockResolvedValue({
    items: [],
    total: 0,
    limit: 100,
    offset: 0,
  });
  vi.spyOn(operatorApiClient, "getConfiguration").mockResolvedValue(configurationFixture);
  vi.spyOn(operatorApiClient, "getProjectOwnershipMigration").mockResolvedValue({
    total_projects: 1,
    locked_projects: 1,
    legacy_unlocked_projects: 0,
    default_organization_unlocked_projects: 0,
    projects: [],
  });
  vi.spyOn(operatorApiClient, "getDocuments").mockResolvedValue({
    items: documents,
    total: 3,
    limit: 100,
    offset: 0,
  });
  vi.spyOn(operatorApiClient, "getJobs").mockResolvedValue({
    items: [],
    total: 0,
    limit: 100,
    offset: 0,
  });
  vi.spyOn(operatorApiClient, "getIndexBuilds").mockResolvedValue({
    items: [],
    active_build_id: null,
    previous_build_id: null,
  });
  vi.spyOn(operatorApiClient, "getSourceState").mockImplementation(() => Promise.resolve(state));
  vi.spyOn(operatorApiClient, "getSourceRevisions").mockResolvedValue([]);
  vi.spyOn(operatorApiClient, "getSourceActivations").mockResolvedValue([]);
  const upload = vi.spyOn(operatorApiClient, "uploadDocument").mockResolvedValue(documents[0]!);
  const save = vi
    .spyOn(operatorApiClient, "createSourceRevision")
    .mockImplementation((_project, document, data) => {
      const item = state.items.find((item) => item.document_id === document)!;
      item.revision = {
        ...item.revision,
        id: "finance-corrected",
        revision_number: 2,
        relationships: (data.relationships ?? []).map((edge, i) => ({
          ...edge,
          id: `edge-${i}`,
          created_at: now,
        })),
      };
      return Promise.resolve({ revision: item.revision, activation: item.activation });
    });
  return { upload, save, state };
}
afterEach(() => vi.restoreAllMocks());

test.each(["projects", "lab"])("%s upload sends both modification targets", async (surface) => {
  const { upload } = setup();
  renderOperatorComponent(
    <OperatorConsoleApp />,
    surface === "lab"
      ? `/lab?project=${projectFixture.id}&tab=documents`
      : `/projects?project=${projectFixture.id}&section=sources`,
  );
  if (surface === "lab") await userEvent.click(await screen.findByText("Source versioning"));
  await userEvent.selectOptions(await screen.findByLabelText("Source treatment"), "modifies");
  const uploadForm =
    screen.getByLabelText("Source treatment").closest("form") ??
    screen.getByLabelText("Source treatment").closest("details")!;
  const choices = within(uploadForm as HTMLElement).getByRole("group", {
    name: /Sources this document modifies/,
  });
  await userEvent.click(within(choices).getByRole("checkbox", { name: /Act Bangla/ }));
  await userEvent.click(within(choices).getByRole("checkbox", { name: /Act English/ }));
  const input =
    surface === "lab"
      ? screen.getByText("Drop a document here").closest("label")!.querySelector("input")!
      : screen.getByLabelText("File");
  await userEvent.upload(input, new File(["amendment"], "finance.txt", { type: "text/plain" }));
  if (surface === "projects") {
    expect((input as HTMLInputElement).files).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Upload" })).toBeEnabled();
    // jsdom native required-file validation does not see user-event's FileList.
    fireEvent.submit(uploadForm);
  } else {
    await userEvent.click(screen.getByRole("button", { name: "Submit document" }));
  }
  await waitFor(() => expect(upload).toHaveBeenCalledOnce());
  expect(upload.mock.calls[0]?.[3]?.relationships).toEqual([
    { relationship_type: "modifies", target_revision_id: base.id, target_provisions: [] },
    { relationship_type: "modifies", target_revision_id: english.id, target_provisions: [] },
  ]);
});

test.each(["projects", "lab"])(
  "%s correction saves and restores the full selected target list",
  async (surface) => {
    const { save } = setup();
    renderOperatorComponent(
      <OperatorConsoleApp />,
      surface === "lab"
        ? `/lab?project=${projectFixture.id}&tab=documents`
        : `/projects?project=${projectFixture.id}&section=sources`,
    );
    if (surface === "lab")
      await userEvent.click(await screen.findByRole("button", { name: "Correct metadata" }));
    await userEvent.selectOptions(await screen.findByLabelText("Correct treatment"), "modifies");
    const choices = screen.getByRole("group", { name: /Sources this document modifies/ });
    expect(
      within(choices).queryByRole("checkbox", { name: /Finance Act/ }),
    ).not.toBeInTheDocument();
    await userEvent.click(within(choices).getByRole("checkbox", { name: /Act Bangla/ }));
    await userEvent.click(within(choices).getByRole("checkbox", { name: /Act English/ }));
    await userEvent.click(screen.getByRole("button", { name: "Save metadata correction" }));
    await waitFor(() => expect(save).toHaveBeenCalledOnce());
    expect(save.mock.calls[0]?.[2].relationships).toHaveLength(2);
    if (surface === "lab")
      await userEvent.click(await screen.findByRole("button", { name: "Correct metadata" }));
    await waitFor(() => expect(screen.getByRole("checkbox", { name: /Act Bangla/ })).toBeChecked());
    expect(screen.getByRole("checkbox", { name: /Act English/ })).toBeChecked();
  },
);
