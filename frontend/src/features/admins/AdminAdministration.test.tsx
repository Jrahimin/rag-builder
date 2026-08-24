import { screen } from "@testing-library/react";
import { vi } from "vitest";
import { operatorApiClient, type AdminUser } from "../../api/operatorApiClient";
import { OperatorConsoleApp } from "../../app/OperatorConsoleApp";
import { renderOperatorComponent } from "../../test/renderOperatorComponent";

const superAdmin: AdminUser = {
  id: "test-admin",
  email: "owner@example.com",
  role: "SUPER_ADMIN",
  is_active: true,
  last_login_at: null,
  deleted_at: null,
  deleted_by: null,
  created_at: "2026-08-16T00:00:00Z",
  updated_at: "2026-08-16T00:00:00Z",
};

const operator: AdminUser = {
  id: "33333333-3333-3333-3333-333333333333",
  email: "ops@example.com",
  role: "ADMIN",
  is_active: true,
  last_login_at: null,
  deleted_at: null,
  deleted_by: null,
  created_at: "2026-08-24T00:00:00Z",
  updated_at: "2026-08-24T00:00:00Z",
};

test("lists operators and shows Super Admin as protected", async () => {
  vi.spyOn(operatorApiClient, "getAdminUsers").mockResolvedValue({
    items: [superAdmin, operator],
    total: 2,
    limit: 100,
    offset: 0,
  });

  renderOperatorComponent(<OperatorConsoleApp />, `/admins?admin=${superAdmin.id}`);
  expect(
    await screen.findByRole("heading", { name: superAdmin.email, level: 2 }),
  ).toBeInTheDocument();
  expect(
    screen.getByText("Bootstrap Super Admin. Created from the CLI and protected in the console."),
  ).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Disable" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Remove" })).not.toBeInTheDocument();
});

test("lets an operator disable and remove another Admin", async () => {
  vi.spyOn(operatorApiClient, "getAdminUsers").mockResolvedValue({
    items: [superAdmin, operator],
    total: 2,
    limit: 100,
    offset: 0,
  });

  renderOperatorComponent(<OperatorConsoleApp />, `/admins?admin=${operator.id}`);
  expect(
    await screen.findByRole("heading", { name: operator.email, level: 2 }),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Disable" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Remove" })).toBeInTheDocument();
});
