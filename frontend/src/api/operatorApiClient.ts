import type { components } from "./generated/openapi";

export type OperatorOverview = components["schemas"]["OperatorOverview"];
export type MetricsSnapshot = components["schemas"]["MetricsSnapshot"];
export type ActiveConfiguration = components["schemas"]["ActiveConfiguration"];
export type DependencyOverview = components["schemas"]["DependencyOverview"];
export type WorkerOverview = components["schemas"]["WorkerOverview"];
export type RecentFailure = components["schemas"]["RecentFailure"];
export type AuditEvent = components["schemas"]["AuditEventResponse"];
export type Project = components["schemas"]["ProjectResponse"];
export type Document = components["schemas"]["DocumentResponse"];
export type Job = components["schemas"]["JobResponse"];
export type JobDetail = components["schemas"]["JobDetailResponse"];
export type EvaluationDataset = components["schemas"]["EvaluationDatasetResponse"];
export type EvaluationRun = components["schemas"]["EvaluationRunResponse"];
export type QualitySummary = components["schemas"]["QualitySummary"];
export type ProjectPage = components["schemas"]["PaginatedResult_ProjectResponse_"];
export type DocumentPage = components["schemas"]["PaginatedResult_DocumentResponse_"];
export type JobPage = components["schemas"]["PaginatedResult_JobResponse_"];

type ApiSuccess<T> = { success: true; data: T | null };
type ApiFailure = {
  success: false;
  error: { code: string; message: string; trace_id?: string | null };
};

export class OperatorApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
    readonly traceId?: string | null,
  ) {
    super(message);
    this.name = "OperatorApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers: { Accept: "application/json", ...init?.headers },
    });
  } catch {
    throw new OperatorApiError(
      "The backend is unavailable. Start the API service and try again.",
      0,
      "backend_unavailable",
    );
  }

  let payload: ApiSuccess<T> | ApiFailure | null = null;
  try {
    payload = (await response.json()) as ApiSuccess<T> | ApiFailure;
  } catch {
    if (!response.ok) {
      if (response.status >= 500) {
        throw new OperatorApiError(
          "The backend is unavailable. Start the API service and try again.",
          response.status,
          "backend_unavailable",
        );
      }
      throw new OperatorApiError(
        "The backend returned an unreadable response.",
        response.status,
        "invalid_response",
      );
    }
  }

  if (!response.ok || payload?.success === false) {
    const failure = payload?.success === false ? payload.error : undefined;
    throw new OperatorApiError(
      failure?.message ?? `Request failed with status ${response.status}.`,
      response.status,
      failure?.code ?? "request_failed",
      failure?.trace_id,
    );
  }
  if (!payload || payload.data === null) {
    throw new OperatorApiError("The backend returned no data.", response.status, "empty_response");
  }
  return payload.data;
}

function query(params: Record<string, string | number | undefined>): string {
  const values = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") values.set(key, String(value));
  }
  const encoded = values.toString();
  return encoded ? `?${encoded}` : "";
}

const apiRoot = "/api/v1";

export const operatorApiClient = {
  getOverview: () => request<OperatorOverview>(`${apiRoot}/operator/overview`),
  getMetrics: () => request<MetricsSnapshot>(`${apiRoot}/operator/metrics`),
  getConfiguration: () => request<ActiveConfiguration>(`${apiRoot}/operator/configuration`),
  getDependencies: () => request<DependencyOverview>(`${apiRoot}/operator/dependencies`),
  getWorkers: () => request<WorkerOverview>(`${apiRoot}/operator/workers`),
  getFailures: (limit = 20) =>
    request<RecentFailure[]>(`${apiRoot}/operator/failures${query({ limit })}`),
  getAuditEvents: (limit = 100, offset = 0) =>
    request<AuditEvent[]>(`${apiRoot}/operator/audit-events${query({ limit, offset })}`),
  getProjects: (limit = 100, offset = 0) =>
    request<ProjectPage>(`${apiRoot}/projects${query({ limit, offset })}`),
  getDocuments: (projectId: string, limit = 100, offset = 0) =>
    request<DocumentPage>(`${apiRoot}/projects/${projectId}/documents${query({ limit, offset })}`),
  getJobs: (
    projectId: string,
    filters: {
      limit?: number;
      offset?: number;
      state?: string;
      jobType?: string;
      documentId?: string;
    } = {},
  ) =>
    request<JobPage>(
      `${apiRoot}/projects/${projectId}/jobs${query({
        limit: filters.limit ?? 100,
        offset: filters.offset ?? 0,
        state: filters.state,
        job_type: filters.jobType,
        document_id: filters.documentId,
      })}`,
    ),
  getJob: (projectId: string, jobId: string) =>
    request<JobDetail>(`${apiRoot}/projects/${projectId}/jobs/${jobId}`),
  retryJob: (projectId: string, jobId: string) =>
    request<Job>(`${apiRoot}/projects/${projectId}/jobs/${jobId}/retry`, { method: "POST" }),
  getQuality: (projectId: string) =>
    request<QualitySummary>(`${apiRoot}/projects/${projectId}/evaluations/quality`),
  getEvaluationDatasets: (projectId: string) =>
    request<EvaluationDataset[]>(`${apiRoot}/projects/${projectId}/evaluations/datasets`),
  createEvaluationRun: (projectId: string, datasetId: string) =>
    request<EvaluationRun>(`${apiRoot}/projects/${projectId}/evaluations/runs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dataset_id: datasetId }),
    }),
};
