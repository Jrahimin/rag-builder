import { screen } from "@testing-library/react";
import { vi } from "vitest";
import { operatorApiClient } from "../api/operatorApiClient";
import { renderOperatorComponent } from "../test/renderOperatorComponent";
import { configurationFixture } from "../test/operatorTestFixtures";
import { OperatorConsoleApp } from "./OperatorConsoleApp";

test("routes directly to the configuration screen", async () => {
  vi.spyOn(operatorApiClient, "getConfiguration").mockResolvedValue(configurationFixture);
  renderOperatorComponent(<OperatorConsoleApp />, "/configuration");
  expect(
    await screen.findByRole("heading", { name: "Configuration", level: 1 }),
  ).toBeInTheDocument();
  expect(await screen.findByText("Read-only configuration")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Configuration" })).toHaveClass("nav-link--active");
});
