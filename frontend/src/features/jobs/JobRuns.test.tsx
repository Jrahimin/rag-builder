import { act, screen } from "@testing-library/react";
import { vi } from "vitest";
import { operatorApiClient } from "../../api/operatorApiClient";
import { renderOperatorComponent } from "../../test/renderOperatorComponent";
import { jobFixture, projectFixture } from "../../test/operatorTestFixtures";
import { JobRuns } from "./JobRuns";

test("shows a useful empty state when there are no projects", async () => {
  vi.spyOn(operatorApiClient, "getProjects").mockResolvedValue({
    items: [],
    total: 0,
    limit: 100,
    offset: 0,
  });
  renderOperatorComponent(<JobRuns />);
  expect(await screen.findByText("No projects yet")).toBeInTheDocument();
});

test("polls active job lists more frequently", async () => {
  vi.useFakeTimers();
  vi.spyOn(operatorApiClient, "getProjects").mockResolvedValue({
    items: [projectFixture],
    total: 1,
    limit: 100,
    offset: 0,
  });
  const jobs = vi.spyOn(operatorApiClient, "getJobs").mockResolvedValue({
    items: [{ ...jobFixture, state: "running" }],
    total: 1,
    limit: 100,
    offset: 0,
  });
  renderOperatorComponent(<JobRuns />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(100);
  });
  expect(jobs).toHaveBeenCalledTimes(1);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3_100);
  });
  expect(jobs).toHaveBeenCalledTimes(2);
});

test("defaults to all projects and combines their jobs", async () => {
  const secondProject = {
    ...projectFixture,
    id: "55555555-5555-5555-5555-555555555555",
    name: "Archive",
  };
  vi.spyOn(operatorApiClient, "getProjects").mockResolvedValue({
    items: [projectFixture, secondProject],
    total: 2,
    limit: 100,
    offset: 0,
  });
  const jobs = vi.spyOn(operatorApiClient, "getJobs").mockImplementation((projectId) =>
    Promise.resolve({
      items: [{ ...jobFixture, id: `${projectId}-job`, project_id: projectId }],
      total: 1,
      limit: 100,
      offset: 0,
    }),
  );

  renderOperatorComponent(<JobRuns />);

  expect(await screen.findAllByRole("button", { name: /Inspect job/ })).toHaveLength(2);
  expect(screen.getByLabelText("Project")).toHaveValue("");
  expect(jobs).toHaveBeenCalledTimes(2);
});
