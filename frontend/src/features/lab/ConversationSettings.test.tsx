import { render, screen, within } from "@testing-library/react";
import { vi } from "vitest";
import { operatorApiClient, type EffectiveProjectAIConfig } from "../../api/operatorApiClient";
import { ConversationSettings } from "./ConversationSettings";

it("shows stale translation and evidence settings beside the current revision", async () => {
  vi.spyOn(operatorApiClient, "getProjectAIConfig").mockResolvedValue({
    configuration: {
      evidence_approach: "factual",
      retrieval: { query_translation_enabled: false },
    },
    provenance: { project_config_revision_number: 2 },
  } as EffectiveProjectAIConfig);
  render(
    <ConversationSettings
      projectId="project"
      snapshot={{
        project_revision: 1,
        evidence_approach: "authoritative",
        translation_enabled: true,
        config_snapshot_id: "old-snapshot",
      }}
    />,
  );
  expect(await screen.findByText(/retains an older configuration/)).toBeInTheDocument();
  const row = screen.getByRole("row", { name: "Query translation On Off" });
  expect(within(row).getByText("Off")).toBeInTheDocument();
  expect(
    screen.getByRole("row", { name: "Evidence approach authoritative factual" }),
  ).toBeInTheDocument();
});

it("keeps saved settings visible when the current Project request fails", async () => {
  vi.spyOn(operatorApiClient, "getProjectAIConfig").mockRejectedValue(new Error("Offline"));
  render(
    <ConversationSettings
      projectId="project"
      snapshot={{
        project_revision: 1,
        evidence_approach: "authoritative",
        translation_enabled: false,
      }}
    />,
  );
  expect(await screen.findByText(/could not be loaded/)).toBeInTheDocument();
  expect(screen.getByText("authoritative")).toBeInTheDocument();
});
