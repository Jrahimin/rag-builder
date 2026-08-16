import { screen } from "@testing-library/react";
import { vi } from "vitest";
import { operatorApiClient, type Organization } from "../../api/operatorApiClient";
import { OperatorConsoleApp } from "../../app/OperatorConsoleApp";
import { projectFixture } from "../../test/operatorTestFixtures";
import { renderOperatorComponent } from "../../test/renderOperatorComponent";

const organization: Organization = {
  id: projectFixture.organization_id,
  name: "Acme Client",
  description: "External client boundary",
  is_active: true,
  deleted_at: null,
  deleted_by: null,
  created_at: "2026-08-16T00:00:00Z",
  updated_at: "2026-08-16T00:00:00Z",
};

test("shows a client record, associated Projects, and non-secret key metadata", async () => {
  vi.spyOn(operatorApiClient, "getOrganizations").mockResolvedValue({
    items: [organization],
    total: 1,
    limit: 100,
    offset: 0,
  });
  vi.spyOn(operatorApiClient, "getOrganizationProjects").mockResolvedValue({
    items: [projectFixture],
    total: 1,
    limit: 100,
    offset: 0,
  });
  vi.spyOn(operatorApiClient, "getApiKeys").mockResolvedValue({
    items: [
      {
        id: "22222222-2222-2222-2222-222222222222",
        organization_id: organization.id,
        name: "production",
        key_prefix: "ape_live_abcd",
        status: "active",
        created_at: "2026-08-16T00:00:00Z",
        updated_at: "2026-08-16T00:00:00Z",
        last_used_at: null,
        revoked_at: null,
        created_by: "test-admin",
        rotated_from_key_id: null,
      },
    ],
    total: 1,
    limit: 100,
    offset: 0,
  });

  renderOperatorComponent(<OperatorConsoleApp />, `/organizations?organization=${organization.id}`);
  expect(
    await screen.findByRole("heading", { name: "Acme Client", level: 2 }, { timeout: 5_000 }),
  ).toBeInTheDocument();
  expect(await screen.findByText("production")).toBeInTheDocument();
  expect(screen.getByText(projectFixture.name)).toBeInTheDocument();
  expect(screen.queryByText(/full_key_shown_once/)).not.toBeInTheDocument();
});
