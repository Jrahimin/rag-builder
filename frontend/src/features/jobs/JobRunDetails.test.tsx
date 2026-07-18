import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { operatorApiClient } from "../../api/operatorApiClient";
import { renderOperatorComponent } from "../../test/renderOperatorComponent";
import { jobDetailFixture, jobFixture, projectFixture } from "../../test/operatorTestFixtures";
import { JobRunDetails } from "./JobRunDetails";

test("shows failure detail and retries only through the typed retry action", async () => {
  vi.spyOn(operatorApiClient, "getJob").mockResolvedValue(jobDetailFixture);
  const retry = vi.spyOn(operatorApiClient, "retryJob").mockResolvedValue({
    ...jobFixture,
    id: "55555555-5555-5555-5555-555555555555",
    state: "queued",
    retry_of_job_id: jobFixture.id,
  });
  renderOperatorComponent(
    <JobRunDetails projectId={projectFixture.id} jobId={jobFixture.id} onClose={() => undefined} />,
  );
  expect(await screen.findByText("Parser rejected the document.")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Retry failed job" }));
  expect(retry).toHaveBeenCalledWith(projectFixture.id, jobFixture.id);
  expect(await screen.findByText(/Retry queued as job/)).toBeInTheDocument();
});

test("does not offer retry for a running job", async () => {
  vi.spyOn(operatorApiClient, "getJob").mockResolvedValue({
    ...jobDetailFixture,
    state: "running",
    failure_code: null,
    failure_message: null,
  });
  renderOperatorComponent(
    <JobRunDetails projectId={projectFixture.id} jobId={jobFixture.id} onClose={() => undefined} />,
  );
  expect(await screen.findByRole("button", { name: "Retry failed job" })).toBeDisabled();
  expect(screen.getByText(/available only after/)).toBeInTheDocument();
});

test("renders reconciliation result fields as a structured report", async () => {
  vi.spyOn(operatorApiClient, "getJob").mockResolvedValue({
    ...jobDetailFixture,
    job_type: "storage.reconcile",
    state: "succeeded",
    progress: 100,
    failure_code: null,
    failure_message: null,
    result: { expected: 12, actual: 11, missing: 1, orphan: 0, consistent: false },
  });
  renderOperatorComponent(
    <JobRunDetails projectId={projectFixture.id} jobId={jobFixture.id} onClose={() => undefined} />,
  );
  expect(await screen.findByRole("heading", { name: "Reconciliation report" })).toBeInTheDocument();
  expect(screen.getByText("Expected")).toBeInTheDocument();
  expect(screen.getByText("Missing")).toBeInTheDocument();
  expect(screen.getByText("No")).toHaveClass("result-inconsistent");
});
