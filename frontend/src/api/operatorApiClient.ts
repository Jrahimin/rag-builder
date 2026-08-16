import type { components } from "./generated/openapi";
import { ADMIN_AUTH_EXPIRED_EVENT, adminAuthApi, getCsrfHeader } from "../auth/adminAuthApi";
import { apiUrl } from "./apiOrigin";

export type OperatorOverview = components["schemas"]["OperatorOverview"];
export type MetricsSnapshot = components["schemas"]["MetricsSnapshot"];
export type UsageReport = components["schemas"]["UsageReport"];
export type UsageBucket = components["schemas"]["UsageBucket"];
export type UsageWorkload = components["schemas"]["UsageWorkload"];
export type ActiveConfiguration = components["schemas"]["ActiveConfiguration"];
export type DependencyOverview = components["schemas"]["DependencyOverview"];
export type WorkerOverview = components["schemas"]["WorkerOverview"];
export type RecentFailure = components["schemas"]["RecentFailure"];
export type AuditEvent = components["schemas"]["AuditEventResponse"];
export type Organization = components["schemas"]["OrganizationResponse"];
export type OrganizationPage = components["schemas"]["PaginatedResult_OrganizationResponse_"];
export type ApiKey = components["schemas"]["ApiKeyResponse"];
export type ApiKeySecret = components["schemas"]["ApiKeySecretResponse"];
export type ApiKeyPage = components["schemas"]["PaginatedResult_ApiKeyResponse_"];
export type Project = components["schemas"]["ProjectResponse"];
export type ProjectOwnershipMigration = components["schemas"]["ProjectOwnershipMigrationStatus"];
export type ProjectOwnershipPreflight = components["schemas"]["ProjectOwnershipPreflight"];
export type EffectiveProjectAIConfig = components["schemas"]["EffectiveProjectAIConfigResponse"];
export type ProjectAIConfig = components["schemas"]["ProjectAIConfig"];
export type ProjectAIConfigRevision = components["schemas"]["ProjectAIConfigRevisionResponse"];
export type ProviderParameterCapability = {
  supported: boolean;
  wire_name: string | null;
  minimum: number | null;
  maximum: number | null;
  omit_when_none: boolean;
};
export type ProviderCapability = {
  provider: string;
  model: string;
  capability_version: string;
  supports_stream_usage: boolean;
  parameters: {
    temperature: ProviderParameterCapability;
    max_tokens: ProviderParameterCapability;
  };
};
export type SourceRevisionCreate = components["schemas"]["SourceRevisionCreate"];
export type SourceRevision = components["schemas"]["SourceRevisionResponse"];
export type SourceRevisionCreated = components["schemas"]["SourceRevisionCreateResponse"];
export type SourceActivation = components["schemas"]["SourceActivationResponse"];
export type SourceState = components["schemas"]["SourceStateResponse"];
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
export type WebhookEndpoint = components["schemas"]["WebhookEndpointResponse"];
export type WebhookEndpointCreated = components["schemas"]["WebhookEndpointCreatedResponse"];
export type WebhookDelivery = components["schemas"]["WebhookDeliveryResponse"];
export type WebhookDeliveryDetail = components["schemas"]["WebhookDeliveryDetailResponse"];
export type WebhookEventType = components["schemas"]["WebhookEventType"];
export type WebhookEndpointPage = components["schemas"]["PaginatedResult_WebhookEndpointResponse_"];
export type WebhookDeliveryPage = components["schemas"]["PaginatedResult_WebhookDeliveryResponse_"];

export type UsageFilters = {
  startAt?: string;
  endAt?: string;
  bucket?: UsageBucket;
  organizationId?: string;
  projectId?: string;
  provider?: string;
  model?: string;
  workload?: UsageWorkload;
};

type ApiSuccess<T> = { success: true; data: T | null };
type ApiFailure = {
  error: { code: string; message: string; request_id?: string | null; details?: object };
};

function isApiFailure<T>(payload: ApiSuccess<T> | ApiFailure | null): payload is ApiFailure {
  return payload !== null && "error" in payload;
}

let refreshInFlight: Promise<boolean> | null = null;

function notifyExpiredSession() {
  window.dispatchEvent(new Event(ADMIN_AUTH_EXPIRED_EVENT));
}

async function refreshSession(): Promise<boolean> {
  if (!refreshInFlight) {
    refreshInFlight = adminAuthApi
      .refresh()
      .then(() => true)
      .catch(() => false)
      .finally(() => {
        refreshInFlight = null;
      });
  }
  return refreshInFlight;
}

export class OperatorApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
    readonly requestId?: string | null,
  ) {
    super(message);
    this.name = "OperatorApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const send = () =>
    fetch(apiUrl(path), {
      ...init,
      credentials: "include",
      headers: {
        Accept: "application/json",
        ...(init?.method && !["GET", "HEAD"].includes(init.method) ? getCsrfHeader() : {}),
        ...init?.headers,
      },
    });

  let response: Response;
  try {
    response = await send();
    if (response.status === 401) {
      if (await refreshSession()) response = await send();
      if (response.status === 401) notifyExpiredSession();
    }
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

  const failure = isApiFailure(payload) ? payload.error : undefined;
  if (!response.ok || failure) {
    throw new OperatorApiError(
      failure?.message ?? `Request failed with status ${response.status}.`,
      response.status,
      failure?.code ?? "request_failed",
      failure?.request_id,
    );
  }
  if (!payload || isApiFailure(payload) || payload.data === null) {
    throw new OperatorApiError("The backend returned no data.", response.status, "empty_response");
  }
  return payload.data;
}

async function getAllOrganizations(includeDeleted = true): Promise<OrganizationPage> {
  const pageSize = 100;
  const items: Organization[] = [];
  let offset = 0;
  let total = 0;
  do {
    const page = await request<OrganizationPage>(
      `${apiRoot}/organizations${query({
        limit: pageSize,
        offset,
        include_deleted: String(includeDeleted),
      })}`,
    );
    items.push(...page.items);
    total = page.total;
    offset += page.items.length;
    if (page.items.length === 0) break;
  } while (items.length < total);
  return { items, total, limit: pageSize, offset: 0 };
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
  getUsage: (filters: UsageFilters = {}) =>
    request<UsageReport>(
      `${apiRoot}/operator/usage${query({
        start_at: filters.startAt,
        end_at: filters.endAt,
        bucket: filters.bucket,
        organization_id: filters.organizationId,
        project_id: filters.projectId,
        provider: filters.provider,
        model: filters.model,
        workload: filters.workload,
      })}`,
    ),
  getConfiguration: () => request<ActiveConfiguration>(`${apiRoot}/operator/configuration`),
  getDependencies: () => request<DependencyOverview>(`${apiRoot}/operator/dependencies`),
  getWorkers: () => request<WorkerOverview>(`${apiRoot}/operator/workers`),
  getFailures: (limit = 20) =>
    request<RecentFailure[]>(`${apiRoot}/operator/failures${query({ limit })}`),
  getAuditEvents: (limit = 100, offset = 0) =>
    request<AuditEvent[]>(`${apiRoot}/operator/audit-events${query({ limit, offset })}`),
  getOrganizations: (includeDeleted = true) => getAllOrganizations(includeDeleted),
  getOrganization: (organizationId: string, includeDeleted = true) =>
    request<Organization>(
      `${apiRoot}/organizations/${organizationId}${query({ include_deleted: String(includeDeleted) })}`,
    ),
  createOrganization: (name: string, description?: string) =>
    request<Organization>(`${apiRoot}/organizations`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, description: description || null }),
    }),
  updateOrganization: (organizationId: string, name: string, description?: string) =>
    request<Organization>(`${apiRoot}/organizations/${organizationId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, description: description || null }),
    }),
  setOrganizationStatus: (organizationId: string, isActive: boolean) =>
    request<Organization>(`${apiRoot}/organizations/${organizationId}/status`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ is_active: isActive }),
    }),
  archiveOrganization: (organizationId: string) =>
    request<Organization>(`${apiRoot}/organizations/${organizationId}/archive`, {
      method: "POST",
    }),
  restoreOrganization: (organizationId: string) =>
    request<Organization>(`${apiRoot}/organizations/${organizationId}/restore`, {
      method: "POST",
    }),
  getOrganizationProjects: (organizationId: string) =>
    request<ProjectPage>(`${apiRoot}/organizations/${organizationId}/projects`),
  getApiKeys: (organizationId: string) =>
    request<ApiKeyPage>(`${apiRoot}/organizations/${organizationId}/api-keys?limit=100`),
  createApiKey: (organizationId: string, name: string) =>
    request<ApiKeySecret>(`${apiRoot}/organizations/${organizationId}/api-keys`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    }),
  rotateApiKey: (
    organizationId: string,
    keyId: string,
    replacementName?: string,
    revokeOld = false,
  ) =>
    request<ApiKeySecret>(`${apiRoot}/organizations/${organizationId}/api-keys/${keyId}/rotate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        replacement_name: replacementName || null,
        revoke_old: revokeOld,
        confirm_immediate_revocation: revokeOld,
      }),
    }),
  revokeApiKey: (organizationId: string, keyId: string) =>
    request<ApiKey>(`${apiRoot}/organizations/${organizationId}/api-keys/${keyId}`, {
      method: "DELETE",
    }),
  getProjects: (limit = 100, offset = 0) =>
    request<ProjectPage>(`${apiRoot}/projects${query({ limit, offset })}`),
  getAllOperatorProjects: (limit = 500, offset = 0) =>
    request<ProjectPage>(
      `${apiRoot}/operator/projects${query({ limit, offset, include_deleted: "true" })}`,
    ),
  getOperatorProject: (projectId: string) =>
    request<Project>(`${apiRoot}/operator/projects/${projectId}`),
  createProject: (name: string, organizationId: string, description?: string) =>
    request<Project>(`${apiRoot}/operator/projects`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name,
        description: description || null,
        organization_id: organizationId,
      }),
    }),
  updateProject: (projectId: string, name: string, description?: string) =>
    request<Project>(`${apiRoot}/operator/projects/${projectId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, description: description || null }),
    }),
  setProjectStatus: (projectId: string, isActive: boolean) =>
    request<Project>(`${apiRoot}/operator/projects/${projectId}/status`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ is_active: isActive }),
    }),
  archiveProject: (projectId: string) =>
    request<Project>(`${apiRoot}/operator/projects/${projectId}/archive`, { method: "POST" }),
  restoreProject: (projectId: string) =>
    request<Project>(`${apiRoot}/operator/projects/${projectId}/restore`, { method: "POST" }),
  getProjectOwnershipMigration: () =>
    request<ProjectOwnershipMigration>(`${apiRoot}/operator/projects/ownership-migration`),
  getProjectOwnershipPreflight: (projectId: string, targetOrganizationId: string) =>
    request<ProjectOwnershipPreflight>(
      `${apiRoot}/operator/projects/${projectId}/ownership/preflight${query({ target_organization_id: targetOrganizationId })}`,
    ),
  reassignProjectOwnership: (
    projectId: string,
    currentOrganizationId: string,
    targetOrganizationId: string,
    reason: string,
  ) =>
    request<Project>(`${apiRoot}/operator/projects/${projectId}/ownership/reassign`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        expected_current_organization_id: currentOrganizationId,
        target_organization_id: targetOrganizationId,
        reason,
      }),
    }),
  confirmProjectOwnership: (projectId: string, organizationId: string, reason: string) =>
    request<Project>(`${apiRoot}/operator/projects/${projectId}/ownership/confirm`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_current_organization_id: organizationId, reason }),
    }),
  getProjectHistory: (projectId: string) =>
    request<AuditEvent[]>(`${apiRoot}/operator/projects/${projectId}/history`),
  getProjectAIConfig: (projectId: string) =>
    request<EffectiveProjectAIConfig>(`${apiRoot}/operator/projects/${projectId}/ai-config`),
  getProjectAIConfigHistory: (projectId: string) =>
    request<ProjectAIConfigRevision[]>(
      `${apiRoot}/operator/projects/${projectId}/ai-config/revisions`,
    ),
  createProjectAIConfig: (
    projectId: string,
    configuration: ProjectAIConfig,
    expectedActiveRevisionId: string | null,
    reason: string,
  ) =>
    request<ProjectAIConfigRevision>(
      `${apiRoot}/operator/projects/${projectId}/ai-config/revisions`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          configuration,
          expected_active_revision_id: expectedActiveRevisionId,
          reason,
        }),
      },
    ),
  restoreProjectAIConfig: (
    projectId: string,
    revisionId: string,
    expectedActiveRevisionId: string | null,
    reason: string,
  ) =>
    request<ProjectAIConfigRevision>(
      `${apiRoot}/operator/projects/${projectId}/ai-config/revisions/${revisionId}/restore`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expected_active_revision_id: expectedActiveRevisionId, reason }),
      },
    ),
  getProviderCapabilities: (provider?: string, model?: string) =>
    request<ProviderCapability[]>(
      `${apiRoot}/operator/provider-capabilities${query({ provider, model })}`,
    ),
  getDocuments: (projectId: string, limit = 100, offset = 0) =>
    request<DocumentPage>(`${apiRoot}/projects/${projectId}/documents${query({ limit, offset })}`),
  getDocument: (projectId: string, documentId: string) =>
    request<Document>(`${apiRoot}/projects/${projectId}/documents/${documentId}`),
  uploadDocument: (
    projectId: string,
    file: File,
    ocrLang?: string,
    sourceMetadata?: SourceRevisionCreate,
  ) => {
    const body = new FormData();
    body.append("file", file);
    if (ocrLang) body.append("ocr_lang", ocrLang);
    if (sourceMetadata) body.append("source_metadata", JSON.stringify(sourceMetadata));
    return request<Document>(`${apiRoot}/projects/${projectId}/documents`, {
      method: "POST",
      body,
    });
  },
  getSourceState: (projectId: string, generation?: number) =>
    request<SourceState>(`${apiRoot}/projects/${projectId}/sources${query({ generation })}`),
  getSourceRevisions: (projectId: string, documentId: string) =>
    request<SourceRevision[]>(
      `${apiRoot}/projects/${projectId}/sources/documents/${documentId}/revisions`,
    ),
  createSourceRevision: (projectId: string, documentId: string, revision: SourceRevisionCreate) =>
    request<SourceRevisionCreated>(
      `${apiRoot}/projects/${projectId}/sources/documents/${documentId}/revisions`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(revision),
      },
    ),
  activateSourceRevision: (projectId: string, revisionId: string, reason: string) =>
    request<SourceActivation>(
      `${apiRoot}/projects/${projectId}/sources/revisions/${revisionId}/activate`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason }),
      },
    ),
  getSourceActivations: (projectId: string, documentId?: string) =>
    request<SourceActivation[]>(
      `${apiRoot}/projects/${projectId}/sources/activations${query({ document_id: documentId })}`,
    ),
  reprocessDocument: (projectId: string, documentId: string, ocrLang?: string) =>
    request<Document>(
      `${apiRoot}/projects/${projectId}/documents/${documentId}/reprocess${query({ ocr_lang: ocrLang })}`,
      {
        method: "POST",
      },
    ),
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
    const send = () =>
      fetch(`${apiRoot}/projects/${projectId}/conversations/${conversationId}/messages/stream`, {
        method: "POST",
        credentials: "include",
        headers: {
          Accept: "text/event-stream",
          "Content-Type": "application/json",
          ...getCsrfHeader(),
        },
        body: JSON.stringify({ content, document_id: documentId ?? null, metadata_filter: {} }),
      });

    let response: Response;
    try {
      response = await send();
      if (response.status === 401) {
        if (await refreshSession()) response = await send();
        if (response.status === 401) notifyExpiredSession();
      }
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
        response.headers.get("x-request-id"),
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
  getWebhookEndpoints: (projectId: string) =>
    request<WebhookEndpointPage>(`${apiRoot}/projects/${projectId}/webhooks/endpoints`),
  createWebhookEndpoint: (projectId: string, url: string, eventTypes: WebhookEventType[]) =>
    request<WebhookEndpointCreated>(`${apiRoot}/projects/${projectId}/webhooks/endpoints`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, event_types: eventTypes }),
    }),
  setWebhookEndpointStatus: (projectId: string, endpointId: string, enabled: boolean) =>
    request<WebhookEndpoint>(
      `${apiRoot}/projects/${projectId}/webhooks/endpoints/${endpointId}/status`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled }),
      },
    ),
  getWebhookDeliveries: (projectId: string) =>
    request<WebhookDeliveryPage>(`${apiRoot}/projects/${projectId}/webhooks/deliveries`),
  getWebhookDelivery: (projectId: string, deliveryId: string) =>
    request<WebhookDeliveryDetail>(
      `${apiRoot}/projects/${projectId}/webhooks/deliveries/${deliveryId}`,
    ),
  replayWebhookDelivery: (projectId: string, deliveryId: string) =>
    request<WebhookDelivery>(
      `${apiRoot}/projects/${projectId}/webhooks/deliveries/${deliveryId}/replay`,
      { method: "POST" },
    ),
};
