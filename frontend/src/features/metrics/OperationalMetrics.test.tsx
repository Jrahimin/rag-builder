import { screen } from "@testing-library/react";
import { vi } from "vitest";
import { operatorApiClient, type UsageReport } from "../../api/operatorApiClient";
import { metricsFixture } from "../../test/operatorTestFixtures";
import { renderOperatorComponent } from "../../test/renderOperatorComponent";
import { OperationalMetrics } from "./OperationalMetrics";

const emptyLatency = { samples: 0, average_ms: null, maximum_ms: null };
const usage: UsageReport = {
  generated_at: "2026-08-16T00:00:00Z",
  start_at: "2026-07-16T00:00:00Z",
  end_at: "2026-08-16T00:00:00Z",
  bucket: "day",
  totals: {
    bucket_start: null,
    organization_id: null,
    organization_name: null,
    project_id: null,
    project_name: null,
    provider: null,
    model: null,
    workload: null,
    request_count: 2,
    error_count: 0,
    records_with_token_usage: 1,
    input_tokens: null,
    output_tokens: null,
    total_tokens: null,
    retrieval_latency: emptyLatency,
    provider_latency: { samples: 2, average_ms: 25, maximum_ms: 30 },
    total_latency: { samples: 2, average_ms: 40, maximum_ms: 50 },
  },
  items: [
    {
      bucket_start: "2026-08-16T00:00:00Z",
      organization_id: "10000000-0000-0000-0000-000000000001",
      organization_name: "Acme",
      project_id: "20000000-0000-0000-0000-000000000001",
      project_name: "Support",
      provider: "openai",
      model: "gpt-test",
      workload: "chat",
      request_count: 2,
      error_count: 0,
      records_with_token_usage: 1,
      input_tokens: null,
      output_tokens: null,
      total_tokens: null,
      retrieval_latency: emptyLatency,
      provider_latency: { samples: 2, average_ms: 25, maximum_ms: 30 },
      total_latency: { samples: 2, average_ms: 40, maximum_ms: 50 },
    },
  ],
};

test("shows grouped execution dimensions and preserves unknown token usage", async () => {
  vi.spyOn(operatorApiClient, "getMetrics").mockResolvedValue(metricsFixture);
  vi.spyOn(operatorApiClient, "getUsage").mockResolvedValue(usage);

  renderOperatorComponent(<OperationalMetrics />);

  expect(await screen.findByText("Execution usage")).toBeInTheDocument();
  expect(screen.getByText("Acme")).toBeInTheDocument();
  expect(screen.getByText("Support")).toBeInTheDocument();
  expect(screen.getByText("openai")).toBeInTheDocument();
  expect(screen.getByText("gpt-test")).toBeInTheDocument();
  expect(screen.getAllByText("Unknown").length).toBeGreaterThan(0);
  expect(screen.getByText("1/2 requests reported usage")).toBeInTheDocument();
});
