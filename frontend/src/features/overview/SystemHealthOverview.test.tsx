import { screen } from "@testing-library/react";
import { vi } from "vitest";
import { operatorApiClient } from "../../api/operatorApiClient";
import { renderOperatorComponent } from "../../test/renderOperatorComponent";
import { overviewFixture } from "../../test/operatorTestFixtures";
import { SystemHealthOverview } from "./SystemHealthOverview";

test("shows loading then an empty-failure healthy overview", async () => {
  vi.spyOn(operatorApiClient, "getOverview").mockResolvedValue(overviewFixture);
  renderOperatorComponent(<SystemHealthOverview />);
  expect(screen.getByRole("status")).toHaveTextContent("Loading deployment overview");
  expect(await screen.findByText("No recent job failures.")).toBeInTheDocument();
  expect(screen.getByText("System state").parentElement).toHaveTextContent("Healthy");
});

test("makes degraded dependencies prominent", async () => {
  vi.spyOn(operatorApiClient, "getOverview").mockResolvedValue({
    ...overviewFixture,
    status: "degraded",
    dependencies: {
      ...overviewFixture.dependencies,
      readiness: {
        ...overviewFixture.dependencies.readiness,
        status: "degraded",
        dependencies: [
          {
            ...overviewFixture.dependencies.readiness.dependencies[0]!,
            state: "degraded",
            action: "Check database connectivity.",
          },
        ],
      },
    },
  });
  renderOperatorComponent(<SystemHealthOverview />);
  expect(await screen.findByText("Deployment is degraded")).toBeInTheDocument();
  expect(screen.getAllByText("degraded", { selector: ".status-badge" })).toHaveLength(2);
});
