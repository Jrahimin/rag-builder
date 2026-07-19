import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { operatorApiClient } from "../../api/operatorApiClient";
import { projectFixture } from "../../test/operatorTestFixtures";
import { renderOperatorComponent } from "../../test/renderOperatorComponent";
import { WebhookDeliveryInspection } from "./WebhookDeliveryInspection";

test("shows configured endpoints and an empty delivery state", async () => {
  vi.spyOn(operatorApiClient, "getProjects").mockResolvedValue({
    items: [projectFixture],
    total: 1,
    limit: 100,
    offset: 0,
  });
  vi.spyOn(operatorApiClient, "getWebhookEndpoints").mockResolvedValue({
    items: [
      {
        id: "77777777-7777-7777-7777-777777777777",
        project_id: projectFixture.id,
        url: "https://customer.example.test/webhooks/ape",
        description: null,
        event_types: ["document.processing.succeeded.v1"],
        is_enabled: true,
        disabled_at: null,
        disabled_reason: null,
        created_at: "2026-07-19T00:00:00Z",
        updated_at: "2026-07-19T00:00:00Z",
      },
    ],
    total: 1,
    limit: 50,
    offset: 0,
  });
  vi.spyOn(operatorApiClient, "getWebhookDeliveries").mockResolvedValue({
    items: [],
    total: 0,
    limit: 50,
    offset: 0,
  });

  renderOperatorComponent(<WebhookDeliveryInspection />);

  expect(await screen.findByText("https://customer.example.test/webhooks/ape")).toBeInTheDocument();
  expect(screen.getByText("No deliveries")).toBeInTheDocument();
});

test("inspects the immutable event and failed HTTP attempt", async () => {
  vi.spyOn(operatorApiClient, "getProjects").mockResolvedValue({
    items: [projectFixture],
    total: 1,
    limit: 100,
    offset: 0,
  });
  vi.spyOn(operatorApiClient, "getWebhookEndpoints").mockResolvedValue({
    items: [],
    total: 0,
    limit: 50,
    offset: 0,
  });
  const delivery = {
    id: "88888888-8888-8888-8888-888888888888",
    project_id: projectFixture.id,
    endpoint_id: "77777777-7777-7777-7777-777777777777",
    event_id: "99999999-9999-9999-9999-999999999999",
    replay_of_delivery_id: null,
    replay_number: 0,
    state: "failed" as const,
    attempt_count: 1,
    max_attempts: 1,
    available_at: "2026-07-19T00:00:00Z",
    last_status_code: 503,
    last_error: "receiver returned HTTP 503",
    delivered_at: null,
    created_at: "2026-07-19T00:00:00Z",
    updated_at: "2026-07-19T00:00:01Z",
  };
  vi.spyOn(operatorApiClient, "getWebhookDeliveries").mockResolvedValue({
    items: [delivery],
    total: 1,
    limit: 50,
    offset: 0,
  });
  vi.spyOn(operatorApiClient, "getWebhookDelivery").mockResolvedValue({
    ...delivery,
    event: {
      id: delivery.event_id,
      project_id: projectFixture.id,
      event_type: "document.processing.failed.v1",
      api_version: 1,
      source_type: "job",
      source_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
      data: { failure_code: "parser_failed" },
      occurred_at: "2026-07-19T00:00:00Z",
      created_at: "2026-07-19T00:00:00Z",
    },
    attempts: [
      {
        id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        attempt_number: 1,
        attempted_at: "2026-07-19T00:00:01Z",
        status_code: 503,
        latency_ms: 4,
        error: "receiver returned HTTP 503",
        response_excerpt: "temporarily down",
      },
    ],
  });

  renderOperatorComponent(<WebhookDeliveryInspection />);
  await userEvent.click(await screen.findByRole("button", { name: "Inspect" }));

  expect(await screen.findByText("document.processing.failed.v1")).toBeInTheDocument();
  expect(screen.getAllByText("receiver returned HTTP 503")).toHaveLength(2);
  expect(screen.getByText("4 ms")).toBeInTheDocument();
});
