import { queryOptions, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { operatorApiClient, type Project } from "./operatorApiClient";

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

export function useDocuments(projectId: string) {
  return useQuery({
    queryKey: operatorQueryKeys.documents(projectId),
    queryFn: () => operatorApiClient.getDocuments(projectId),
    enabled: Boolean(projectId),
    refetchInterval: 15_000,
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
