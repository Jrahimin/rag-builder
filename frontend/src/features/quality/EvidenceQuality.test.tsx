import { screen } from "@testing-library/react";
import { vi } from "vitest";
import {
  operatorApiClient,
  type EvaluationDataset,
  type QualitySummary,
} from "../../api/operatorApiClient";
import { renderOperatorComponent } from "../../test/renderOperatorComponent";
import { now, projectFixture } from "../../test/operatorTestFixtures";
import { EvidenceQuality } from "./EvidenceQuality";

const dataset: EvaluationDataset = {
  id: "55555555-5555-5555-5555-555555555555",
  project_id: projectFixture.id,
  name: "phase4-representative",
  version: "1.0.0",
  schema_version: 1,
  description: null,
  dataset_hash: "a".repeat(64),
  cases: [],
  created_at: now,
};

const quality: QualitySummary = {
  dataset,
  last_run: {
    id: "66666666-6666-6666-6666-666666666666",
    project_id: projectFixture.id,
    dataset_id: dataset.id,
    job_id: "77777777-7777-7777-7777-777777777777",
    job_state: "succeeded",
    top_k: 5,
    configuration_hash: "b".repeat(64),
    versions: {
      prompt_version: "v2",
      retrieval: { embedding_set_version: 1 },
      corpus: { fingerprint: "c".repeat(64) },
    },
    metrics: {
      reranked_lexical: {
        recall_at_k: 1,
        mrr: 1,
        ndcg: 1,
        groundedness: 1,
        citation_coverage: 1,
        refusal_accuracy: 1,
        latency_p95_ms: 42,
      },
    },
    case_results: [],
    regressions: [],
    failed_cases: [],
    reranker_comparison: {
      active_profile: "reranked_lexical",
      promotion_reason: "no_learned_candidate_met_all_acceptance_thresholds",
      candidates: [],
    },
    completed_at: now,
    created_at: now,
  },
  acceptance_thresholds: {},
};

test("renders reproducible quality metrics for the selected project", async () => {
  vi.spyOn(operatorApiClient, "getProjects").mockResolvedValue({
    items: [projectFixture],
    total: 1,
    limit: 100,
    offset: 0,
  });
  vi.spyOn(operatorApiClient, "getQuality").mockResolvedValue(quality);
  vi.spyOn(operatorApiClient, "getEvaluationDatasets").mockResolvedValue([dataset]);

  renderOperatorComponent(<EvidenceQuality />);

  expect(await screen.findByText("Recall@k")).toBeInTheDocument();
  expect((await screen.findAllByText("100.0%")).length).toBeGreaterThan(0);
  expect(screen.getByText("Configuration hash")).toBeInTheDocument();
});
