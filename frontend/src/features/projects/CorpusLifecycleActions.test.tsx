import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { operatorApiClient, type IndexBuild } from "../../api/operatorApiClient";
import { renderOperatorComponent } from "../../test/renderOperatorComponent";
import { jobDetailFixture, now, projectFixture } from "../../test/operatorTestFixtures";
import { CorpusLifecycleActions } from "./CorpusLifecycleActions";

const build: IndexBuild = {
  id: "88888888-8888-8888-8888-888888888888",
  project_id: projectFixture.id,
  job_id: "99999999-9999-9999-9999-999999999999",
  state: "validated",
  operation: "reindex",
  embedding_set_version: 1,
  configuration_hash: "a".repeat(64),
  corpus_fingerprint: "b".repeat(64),
  document_count: 1,
  chunk_count: 2,
  vector_count: 2,
  keyword_count: 2,
  manifest: {},
  validated_at: now,
  activated_at: null,
  failure_code: null,
  failure_message: null,
  created_at: now,
  updated_at: now,
};

test("confirms and activates only a validated immutable build", async () => {
  vi.spyOn(operatorApiClient, "getIndexBuilds").mockResolvedValue({
    items: [build],
    active_build_id: null,
    previous_build_id: null,
  });
  const activate = vi.spyOn(operatorApiClient, "activateIndexBuild").mockResolvedValue({
    ...build,
    state: "active",
    activated_at: now,
  });
  renderOperatorComponent(<CorpusLifecycleActions projectId={projectFixture.id} />);
  await userEvent.click(await screen.findByRole("button", { name: "Activate" }));

  expect(activate).toHaveBeenCalledWith(projectFixture.id, build.id);
  expect(screen.getByRole("button", { name: /^Rollback/ })).toBeDisabled();
});

test("shows the generated lifecycle job immediately and links to its detail", async () => {
  vi.spyOn(operatorApiClient, "getIndexBuilds").mockResolvedValue({
    items: [build],
    active_build_id: null,
    previous_build_id: null,
  });
  vi.spyOn(operatorApiClient, "reindexCorpus").mockResolvedValue({
    job_id: jobDetailFixture.id,
    build_id: build.id,
    created: true,
  });
  vi.spyOn(operatorApiClient, "getJob").mockResolvedValue({
    ...jobDetailFixture,
    state: "succeeded",
    failure_code: null,
    failure_message: null,
  });
  const notice = vi.fn();
  renderOperatorComponent(
    <CorpusLifecycleActions projectId={projectFixture.id} onNotice={notice} />,
  );
  await userEvent.click(await screen.findByRole("button", { name: /^Reindex/ }));
  await userEvent.click(screen.getByRole("button", { name: "Confirm action" }));
  const link = await screen.findByRole("link", { name: jobDetailFixture.id });
  expect(link).toHaveAttribute(
    "href",
    `/jobs?project=${projectFixture.id}&job=${jobDetailFixture.id}`,
  );
  expect(notice).toHaveBeenCalledWith(expect.objectContaining({ outcome: "accepted" }));
});
