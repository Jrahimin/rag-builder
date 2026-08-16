import { screen } from "@testing-library/react";
import { vi } from "vitest";
import { operatorApiClient, type Organization } from "../../api/operatorApiClient";
import { OperatorConsoleApp } from "../../app/OperatorConsoleApp";
import { projectFixture } from "../../test/operatorTestFixtures";
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

test("uses canonical Project administration and shows locked Organization ownership", async () => {
  vi.spyOn(operatorApiClient, "getAllOperatorProjects").mockResolvedValue({
    items: [projectFixture],
    total: 1,
    limit: 500,
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

  renderOperatorComponent(<OperatorConsoleApp />, `/projects?project=${projectFixture.id}`);
  expect(
    await screen.findByRole("heading", { name: projectFixture.name, level: 2 }, { timeout: 5_000 }),
  ).toBeInTheDocument();
  expect(screen.getByText("Acme Client")).toBeInTheDocument();
  expect(screen.getByText("Immutable")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /Create test project/i })).not.toBeInTheDocument();
});
