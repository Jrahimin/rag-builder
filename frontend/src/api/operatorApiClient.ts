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
export type IndexBuild = components["schemas"]["IndexBuildResponse"];
export type IndexBuildList = components["schemas"]["IndexBuildListResponse"];
export type LifecycleJob = components["schemas"]["LifecycleJobResponse"];
export type SearchRequest = components["schemas"]["SearchRequest"];
export type SearchResponse = components["schemas"]["SearchResponse"];
export type Conversation = components["schemas"]["ConversationResponse"];
export type Message = components["schemas"]["MessageResponse"];
export type ChatTurn = components["schemas"]["ChatTurnResponse"];
export type StreamMessageResult = { content: string };
export type ProjectPage = components["schemas"]["PaginatedResult_ProjectResponse_"];
export type DocumentPage = components["schemas"]["PaginatedResult_DocumentResponse_"];
export type JobPage = components["schemas"]["PaginatedResult_JobResponse_"];
export type ConversationPage = components["schemas"]["PaginatedResult_ConversationResponse_"];
export type MessagePage = components["schemas"]["PaginatedResult_MessageResponse_"];

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
  createProject: (name: string, description?: string) =>
    request<Project>(`${apiRoot}/projects`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, description }),
    }),
  getDocuments: (projectId: string, limit = 100, offset = 0) =>
    request<DocumentPage>(`${apiRoot}/projects/${projectId}/documents${query({ limit, offset })}`),
  getDocument: (projectId: string, documentId: string) =>
    request<Document>(`${apiRoot}/projects/${projectId}/documents/${documentId}`),
  uploadDocument: (projectId: string, file: File, ocrLang?: string) => {
    const body = new FormData();
    body.append("file", file);
    if (ocrLang) body.append("ocr_lang", ocrLang);
    return request<Document>(`${apiRoot}/projects/${projectId}/documents`, {
      method: "POST",
      body,
    });
  },
  reprocessDocument: (projectId: string, documentId: string) =>
    request<Document>(`${apiRoot}/projects/${projectId}/documents/${documentId}/reprocess`, {
      method: "POST",
    }),
  embedDocument: (projectId: string, documentId: string) =>
    request<Document>(`${apiRoot}/projects/${projectId}/documents/${documentId}/embed`, {
      method: "POST",
    }),
  indexDocument: (projectId: string, documentId: string) =>
    request<Document>(`${apiRoot}/projects/${projectId}/documents/${documentId}/index`, {
      method: "POST",
    }),
  deleteDocument: (projectId: string, documentId: string) =>
    request<Document>(`${apiRoot}/projects/${projectId}/documents/${documentId}`, {
      method: "DELETE",
    }),
  purgeDocument: (projectId: string, documentId: string) =>
    request<Document>(`${apiRoot}/projects/${projectId}/documents/${documentId}/purge`, {
      method: "DELETE",
    }),
  getIndexBuilds: (projectId: string) =>
    request<IndexBuildList>(`${apiRoot}/projects/${projectId}/index-builds`),
  reembedCorpus: (projectId: string) =>
    request<LifecycleJob>(`${apiRoot}/projects/${projectId}/index-builds/reembed`, {
      method: "POST",
    }),
  reindexCorpus: (projectId: string) =>
    request<LifecycleJob>(`${apiRoot}/projects/${projectId}/index-builds/reindex`, {
      method: "POST",
    }),
  reconcileStorage: (projectId: string) =>
    request<LifecycleJob>(`${apiRoot}/projects/${projectId}/index-builds/reconcile-storage`, {
      method: "POST",
    }),
  activateIndexBuild: (projectId: string, buildId: string) =>
    request<IndexBuild>(`${apiRoot}/projects/${projectId}/index-builds/${buildId}/activate`, {
      method: "POST",
    }),
  rollbackIndexBuild: (projectId: string) =>
    request<IndexBuild>(`${apiRoot}/projects/${projectId}/index-builds/rollback`, {
      method: "POST",
    }),
  search: (projectId: string, body: SearchRequest) =>
    request<SearchResponse>(`${apiRoot}/projects/${projectId}/search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  getConversations: (projectId: string, limit = 100, offset = 0) =>
    request<ConversationPage>(
      `${apiRoot}/projects/${projectId}/conversations${query({ limit, offset })}`,
    ),
  createConversation: (projectId: string, title?: string) =>
    request<Conversation>(`${apiRoot}/projects/${projectId}/conversations`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: title ?? null }),
    }),
  getConversation: (projectId: string, conversationId: string) =>
    request<Conversation>(`${apiRoot}/projects/${projectId}/conversations/${conversationId}`),
  getMessages: (projectId: string, conversationId: string, limit = 200, offset = 0) =>
    request<MessagePage>(
      `${apiRoot}/projects/${projectId}/conversations/${conversationId}/messages${query({ limit, offset })}`,
    ),
  sendMessage: (projectId: string, conversationId: string, content: string, documentId?: string) =>
    request<ChatTurn>(`${apiRoot}/projects/${projectId}/conversations/${conversationId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content, document_id: documentId ?? null, metadata_filter: {} }),
    }),
  streamMessage: async (
    projectId: string,
    conversationId: string,
    content: string,
    onDelta: (delta: string) => void,
    documentId?: string,
  ): Promise<StreamMessageResult> => {
    let response: Response;
    try {
      response = await fetch(
        `${apiRoot}/projects/${projectId}/conversations/${conversationId}/messages/stream`,
        {
          method: "POST",
          headers: { Accept: "text/event-stream", "Content-Type": "application/json" },
          body: JSON.stringify({ content, document_id: documentId ?? null, metadata_filter: {} }),
        },
      );
    } catch {
      throw new OperatorApiError(
        "The backend is unavailable. Start the API service and try again.",
        0,
        "backend_unavailable",
      );
    }
    if (!response.ok || !response.body) {
      throw new OperatorApiError(
        "The streaming response could not be started.",
        response.status,
        "stream_unavailable",
        response.headers.get("x-trace-id"),
      );
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let streamed = "";
    const consume = (frame: string) => {
      const data = frame
        .split("\n")
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trim())
        .join("\n");
      if (!data) return;
      const event = JSON.parse(data) as { event?: string; delta?: string; message?: string };
      if (event.event === "error") {
        throw new OperatorApiError(
          event.message ?? "The streamed message failed.",
          502,
          "stream_failed",
        );
      }
      if (event.event === "token" && event.delta) {
        streamed += event.delta;
        onDelta(event.delta);
      }
    };
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
      const frames = buffer.split("\n\n");
      buffer = frames.pop() ?? "";
      frames.forEach(consume);
      if (done) break;
    }
    if (buffer.trim()) consume(buffer);
    return { content: streamed };
  },
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
