import { queryOptions, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  operatorApiClient,
  type IndexBuild,
  type LifecycleJob,
  type Project,
} from "./operatorApiClient";

export const operatorQueryKeys = {
  all: ["operator"] as const,
  overview: ["operator", "overview"] as const,
  metrics: ["operator", "metrics"] as const,
  configuration: ["operator", "configuration"] as const,
  dependencies: ["operator", "dependencies"] as const,
  workers: ["operator", "workers"] as const,
  failures: ["operator", "failures"] as const,
  audit: ["operator", "audit"] as const,
  projects: ["operator", "projects"] as const,
  documents: (projectId: string) => ["operator", "projects", projectId, "documents"] as const,
  document: (projectId: string, documentId: string) =>
    ["operator", "projects", projectId, "documents", documentId] as const,
  jobsBase: (projectId: string) => ["operator", "projects", projectId, "jobs"] as const,
  jobs: (projectId: string, state = "", jobType = "") =>
    [...operatorQueryKeys.jobsBase(projectId), state, jobType] as const,
  jobsAll: (projectIds: string[], state = "", jobType = "") =>
    ["operator", "projects", "all", "jobs", projectIds, state, jobType] as const,
  job: (projectId: string, jobId: string) =>
    ["operator", "projects", projectId, "jobs", jobId] as const,
  quality: (projectId: string) => ["operator", "projects", projectId, "quality"] as const,
  evaluationDatasets: (projectId: string) =>
    ["operator", "projects", projectId, "evaluation-datasets"] as const,
  indexBuilds: (projectId: string) => ["operator", "projects", projectId, "index-builds"] as const,
  conversations: (projectId: string) =>
    ["operator", "projects", projectId, "conversations"] as const,
  messages: (projectId: string, conversationId: string) =>
    ["operator", "projects", projectId, "conversations", conversationId, "messages"] as const,
  webhookEndpoints: (projectId: string) =>
    ["operator", "projects", projectId, "webhook-endpoints"] as const,
  webhookDeliveries: (projectId: string) =>
    ["operator", "projects", projectId, "webhook-deliveries"] as const,
  webhookDelivery: (projectId: string, deliveryId: string) =>
    ["operator", "projects", projectId, "webhook-deliveries", deliveryId] as const,
};

export const overviewQueryOptions = queryOptions({
  queryKey: operatorQueryKeys.overview,
  queryFn: () => operatorApiClient.getOverview(),
  refetchInterval: 15_000,
});

export function useProjects() {
  return useQuery({
    queryKey: operatorQueryKeys.projects,
    queryFn: () => operatorApiClient.getProjects(),
    staleTime: 30_000,
  });
}

export function useCreateProject() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ name, description }: { name: string; description?: string }) =>
      operatorApiClient.createProject(name, description),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: operatorQueryKeys.projects });
    },
  });
}

export function useDocuments(projectId: string) {
  return useQuery({
    queryKey: operatorQueryKeys.documents(projectId),
    queryFn: () => operatorApiClient.getDocuments(projectId),
    enabled: Boolean(projectId),
    refetchInterval: 15_000,
  });
}

export function useDocument(projectId: string, documentId: string) {
  return useQuery({
    queryKey: operatorQueryKeys.document(projectId, documentId),
    queryFn: () => operatorApiClient.getDocument(projectId, documentId),
    enabled: Boolean(projectId && documentId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && !["ready", "failed"].includes(status) ? 2_000 : false;
    },
  });
}

export function useUploadDocument(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ file, ocrLang }: { file: File; ocrLang?: string }) =>
      operatorApiClient.uploadDocument(projectId, file, ocrLang),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: operatorQueryKeys.documents(projectId) }),
        queryClient.invalidateQueries({ queryKey: operatorQueryKeys.jobsBase(projectId) }),
      ]);
    },
  });
}

export function useJobs(projectId: string, state: string, jobType: string) {
  return useQuery({
    queryKey: operatorQueryKeys.jobs(projectId, state, jobType),
    queryFn: () => operatorApiClient.getJobs(projectId, { state, jobType }),
    enabled: Boolean(projectId),
    refetchInterval: (query) => {
      const jobs = query.state.data?.items ?? [];
      return jobs.some((job) => ["queued", "running", "retry_scheduled"].includes(job.state))
        ? 3_000
        : 15_000;
    },
  });
}

export function useAllJobs(projects: Project[], state: string, jobType: string, enabled = true) {
  const projectIds = projects.map((project) => project.id);
  return useQuery({
    queryKey: operatorQueryKeys.jobsAll(projectIds, state, jobType),
    queryFn: async () => {
      const pages = await Promise.all(
        projects.map((project) => operatorApiClient.getJobs(project.id, { state, jobType })),
      );
      const items = pages
        .flatMap((page) => page.items)
        .sort((left, right) => right.created_at.localeCompare(left.created_at));
      return {
        items,
        total: pages.reduce((total, page) => total + page.total, 0),
        limit: items.length,
        offset: 0,
      };
    },
    enabled: enabled && projects.length > 0,
    refetchInterval: (query) => {
      const jobs = query.state.data?.items ?? [];
      return jobs.some((job) => ["queued", "running", "retry_scheduled"].includes(job.state))
        ? 3_000
        : 15_000;
    },
  });
}

export function useJob(projectId: string, jobId: string) {
  return useQuery({
    queryKey: operatorQueryKeys.job(projectId, jobId),
    queryFn: () => operatorApiClient.getJob(projectId, jobId),
    enabled: Boolean(projectId && jobId),
    refetchInterval: (query) => {
      const state = query.state.data?.state;
      return state && ["queued", "running", "retry_scheduled"].includes(state) ? 2_000 : false;
    },
  });
}

export function useRetryJob(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) => operatorApiClient.retryJob(projectId, jobId),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: operatorQueryKeys.jobsBase(projectId) }),
        queryClient.invalidateQueries({ queryKey: operatorQueryKeys.overview }),
        queryClient.invalidateQueries({ queryKey: operatorQueryKeys.failures }),
      ]);
    },
  });
}

export function useQuality(projectId: string) {
  return useQuery({
    queryKey: operatorQueryKeys.quality(projectId),
    queryFn: () => operatorApiClient.getQuality(projectId),
    enabled: Boolean(projectId),
    refetchInterval: (query) => {
      const state = query.state.data?.last_run?.job_state;
      return state && ["queued", "running", "retry_scheduled"].includes(state) ? 3_000 : 15_000;
    },
  });
}

export function useEvaluationDatasets(projectId: string) {
  return useQuery({
    queryKey: operatorQueryKeys.evaluationDatasets(projectId),
    queryFn: () => operatorApiClient.getEvaluationDatasets(projectId),
    enabled: Boolean(projectId),
    staleTime: 30_000,
  });
}

export function useCreateEvaluationRun(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (datasetId: string) => operatorApiClient.createEvaluationRun(projectId, datasetId),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: operatorQueryKeys.quality(projectId) }),
        queryClient.invalidateQueries({ queryKey: operatorQueryKeys.jobsBase(projectId) }),
      ]);
    },
  });
}

export function useIndexBuilds(projectId: string) {
  return useQuery({
    queryKey: operatorQueryKeys.indexBuilds(projectId),
    queryFn: () => operatorApiClient.getIndexBuilds(projectId),
    enabled: Boolean(projectId),
    refetchInterval: 5_000,
  });
}

export function useCorpusLifecycleAction(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation<
    LifecycleJob | IndexBuild,
    Error,
    "reembed" | "reindex" | "reconcile" | "rollback"
  >({
    mutationFn: (action: "reembed" | "reindex" | "reconcile" | "rollback") => {
      if (action === "reembed") return operatorApiClient.reembedCorpus(projectId);
      if (action === "reindex") return operatorApiClient.reindexCorpus(projectId);
      if (action === "reconcile") return operatorApiClient.reconcileStorage(projectId);
      return operatorApiClient.rollbackIndexBuild(projectId);
    },
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: operatorQueryKeys.indexBuilds(projectId) }),
        queryClient.invalidateQueries({ queryKey: operatorQueryKeys.jobsBase(projectId) }),
        queryClient.invalidateQueries({ queryKey: operatorQueryKeys.audit }),
      ]);
    },
  });
}

export function useActivateIndexBuild(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (buildId: string) => operatorApiClient.activateIndexBuild(projectId, buildId),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: operatorQueryKeys.indexBuilds(projectId) }),
        queryClient.invalidateQueries({ queryKey: operatorQueryKeys.audit }),
      ]);
    },
  });
}

export function useSearch(projectId: string) {
  return useMutation({
    mutationFn: (body: Parameters<typeof operatorApiClient.search>[1]) =>
      operatorApiClient.search(projectId, body),
  });
}

export function useConversations(projectId: string) {
  return useQuery({
    queryKey: operatorQueryKeys.conversations(projectId),
    queryFn: () => operatorApiClient.getConversations(projectId),
    enabled: Boolean(projectId),
    staleTime: 10_000,
  });
}

export function useCreateConversation(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (title?: string) => operatorApiClient.createConversation(projectId, title),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: operatorQueryKeys.conversations(projectId) });
    },
  });
}

export function useMessages(projectId: string, conversationId: string) {
  return useQuery({
    queryKey: operatorQueryKeys.messages(projectId, conversationId),
    queryFn: () => operatorApiClient.getMessages(projectId, conversationId),
    enabled: Boolean(projectId && conversationId),
  });
}

export function useSendMessage(projectId: string, conversationId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ content, documentId }: { content: string; documentId?: string }) =>
      operatorApiClient.sendMessage(projectId, conversationId, content, documentId),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: operatorQueryKeys.messages(projectId, conversationId),
        }),
        queryClient.invalidateQueries({ queryKey: operatorQueryKeys.conversations(projectId) }),
      ]);
    },
  });
}

export function useStreamMessage(projectId: string, conversationId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      content,
      documentId,
      onDelta,
    }: {
      content: string;
      documentId?: string;
      onDelta: (delta: string) => void;
    }) => {
      await operatorApiClient.streamMessage(
        projectId,
        conversationId,
        content,
        onDelta,
        documentId,
      );
      const page = await operatorApiClient.getMessages(projectId, conversationId);
      const assistant = [...page.items].reverse().find((message) => message.role === "assistant");
      const user = [...page.items].reverse().find((message) => message.role === "user");
      if (!assistant || !user)
        throw new Error("The streamed response was not saved by the backend.");
      return { user_message: user, assistant_message: assistant };
    },
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: operatorQueryKeys.messages(projectId, conversationId),
        }),
        queryClient.invalidateQueries({ queryKey: operatorQueryKeys.conversations(projectId) }),
      ]);
    },
  });
}

export function useDocumentLifecycleAction(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      documentId,
      action,
    }: {
      documentId: string;
      action: "reprocess" | "embed" | "index" | "delete" | "purge";
    }) => {
      if (action === "reprocess") return operatorApiClient.reprocessDocument(projectId, documentId);
      if (action === "embed") return operatorApiClient.embedDocument(projectId, documentId);
      if (action === "index") return operatorApiClient.indexDocument(projectId, documentId);
      if (action === "delete") return operatorApiClient.deleteDocument(projectId, documentId);
      return operatorApiClient.purgeDocument(projectId, documentId);
    },
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: operatorQueryKeys.documents(projectId) }),
        queryClient.invalidateQueries({ queryKey: operatorQueryKeys.jobsBase(projectId) }),
        queryClient.invalidateQueries({ queryKey: operatorQueryKeys.indexBuilds(projectId) }),
        queryClient.invalidateQueries({ queryKey: operatorQueryKeys.audit }),
      ]);
    },
  });
}
