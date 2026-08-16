import { fireEvent, screen } from "@testing-library/react";
import { vi } from "vitest";
import { operatorApiClient, type AuditEvent } from "../../api/operatorApiClient";
import { renderOperatorComponent } from "../../test/renderOperatorComponent";
import { AuditHistory } from "./AuditHistory";

const event: AuditEvent = {
  id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
  created_at: "2026-08-12T06:52:00Z",
  event_type: "job.failed",
  outcome: "failure",
  actor_type: "worker",
  actor_id: "worker-desktop",
  resource_type: "job_run",
  resource_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
  project_id: "cccccccc-cccc-cccc-cccc-cccccccccccc",
  organization_id: "dddddddd-dddd-dddd-dddd-dddddddddddd",
  detail: { document_id: "e9fbe249-1111-2222-3333-444444444444", error: "embed timeout" },
};

test("opens a full audit payload inspector instead of truncating detail", async () => {
  vi.spyOn(operatorApiClient, "getAuditEvents").mockResolvedValue([event]);
  renderOperatorComponent(<AuditHistory />);

  expect(await screen.findByText("job.failed")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Inspect" }));
  expect(await screen.findByRole("dialog", { name: "job.failed" })).toBeInTheDocument();
  expect(screen.getByRole("dialog")).toHaveTextContent("embed timeout");
  expect(screen.getByRole("dialog")).toHaveTextContent(event.resource_id);
});
