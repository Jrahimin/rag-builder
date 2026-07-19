import type {
  ActiveConfiguration,
  DependencyOverview,
  Job,
  JobDetail,
  MetricsSnapshot,
  OperatorOverview,
  Project,
  WorkerOverview,
} from "../api/operatorApiClient";

export const now = "2026-07-18T04:00:00Z";

export const metricsFixture: MetricsSnapshot = {
  generated_at: now,
  jobs: {
    total: 4,
    by_state: { running: 1, failed: 1, succeeded: 2 },
    queued: 0,
    running: 1,
    retry_scheduled: 0,
    failures_24h: 1,
    retry_attempts: 1,
    oldest_queue_age_seconds: null,
    pending_dispatches: 0,
    oldest_dispatch_age_seconds: null,
    dispatch_attempts: 4,
  },
  job_latency: [{ name: "document.process", count: 2, average_ms: 100, maximum_ms: 180 }],
  provider_generation_latency: [],
  retrieval_latency: { name: "retrieval", count: 2, average_ms: 40, maximum_ms: 60 },
  generation_latency: { name: "generation", count: 2, average_ms: 80, maximum_ms: 110 },
  token_usage: { input_tokens: 100, output_tokens: 50, total_tokens: 150 },
  corpus: { projects: 1, documents: 2, chunks: 12, storage_bytes: 4096 },
  active_embedding_set_version: 1,
};

export const dependencyFixture: DependencyOverview = {
  readiness: {
    status: "ready",
    service: "APE",
    version: "0.9.0",
    environment: "development",
    dependencies: [
      {
        name: "postgresql",
        state: "ok",
        detail: "Connected",
        latency_ms: 4,
        checked_at: now,
        cached: true,
      },
    ],
  },
  startup_profile: "development",
  startup_checked_at: now,
};

export const workerFixture: WorkerOverview = {
  available: true,
  active_count: 1,
  stale_after_seconds: 30,
  workers: [
    {
      worker_id: "worker-1",
      hostname: "host",
      process_id: 1,
      queue: "ape",
      version: "0.9.0",
      started_at: now,
      heartbeat_at: now,
      heartbeat_age_seconds: 1,
      state: "active",
    },
  ],
  detail: null,
};

export const overviewFixture: OperatorOverview = {
  status: "ready",
  dependencies: dependencyFixture,
  workers: workerFixture,
  metrics: metricsFixture,
  recent_failures: [],
};

export const projectFixture: Project = {
  id: "11111111-1111-1111-1111-111111111111",
  name: "Knowledge Base",
  description: null,
  is_active: true,
  deleted_at: null,
  deleted_by: null,
  created_at: now,
  updated_at: now,
};

export const jobFixture: Job = {
  id: "22222222-2222-2222-2222-222222222222",
  project_id: projectFixture.id,
  job_type: "document.process",
  state: "failed",
  stage: "parsing",
  progress: 30,
  attempt_count: 3,
  max_attempts: 3,
  idempotency_key: "safe-key",
  document_id: "33333333-3333-3333-3333-333333333333",
  configuration_snapshot_id: "44444444-4444-4444-4444-444444444444",
  retry_of_job_id: null,
  next_attempt_at: null,
  lease_expires_at: null,
  heartbeat_at: null,
  queued_at: now,
  started_at: now,
  completed_at: now,
  failure_code: "parse_failed",
  failure_message: "Parser rejected the document.",
  failure_details: null,
  result: null,
  created_at: now,
  updated_at: now,
};

export const jobDetailFixture: JobDetail = {
  ...jobFixture,
  payload: { document_version: 1 },
  configuration_hash: "a".repeat(64),
  configuration_schema_version: 1,
  configuration: { parsing: { strategy: "auto" } },
};

export const configurationFixture: ActiveConfiguration = {
  environment: "development",
  runtime_profile: "development",
  application_version: "0.9.0",
  llm: { backend: "echo", model: "echo", provider_version: null, credential_configured: null },
  embedding: {
    backend: "hash",
    model: "hash",
    dimensions: 384,
    provider_version: null,
    credential_configured: null,
  },
  reranker_backend: "lexical",
  ocr_backend: "disabled",
  ocr_enabled: false,
  storage_backend: "local",
  job_backend: "inline",
  retrieval_strategy: "hybrid",
  embedding_set_version: 1,
  recent_project_snapshots: [],
};
