import {
  Activity,
  Check,
  ChevronRight,
  Clipboard,
  Clock,
  Copy,
  FileText,
  FlaskConical,
  GitBranch,
  MessageSquare,
  Plus,
  Quote,
  Search,
  Send,
  Sparkles,
  UploadCloud,
  X,
} from "lucide-react";
import {
  type DragEvent,
  type FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  OperatorApiError,
  type ChatTurn,
  type Document,
  type Job,
  type Message,
  type Project,
  type SearchResponse,
  type SourceRevisionCreate,
  type SourceState,
} from "../../api/operatorApiClient";
import {
  useCreateConversation,
  useDocumentLifecycleAction,
  useDocuments,
  useIndexBuilds,
  useJobs,
  useMessages,
  useProjects,
  useSearch,
  useSendMessage,
  useSourceState,
  useStreamMessage,
  useUploadDocument,
} from "../../api/operatorConsoleQueries";
import { EmptyState, ErrorState, LoadingState } from "../../components/QueryStatePanel";
import { ProjectSelector } from "../../components/ProjectSelector";
import { StatusBadge } from "../../components/StatusBadge";
import { formatBytes, formatDate, formatDuration, shortId } from "../../shared/formatters";
import { CorpusLifecycleActions } from "../projects/CorpusLifecycleActions";

const tabs = ["journey", "documents", "search", "messages", "lifecycle"] as const;
type LabTab = (typeof tabs)[number];
const tabDetails = {
  journey: { label: "Journey", icon: FlaskConical },
  documents: { label: "Documents", icon: FileText },
  search: { label: "Search", icon: Search },
  messages: { label: "Messages", icon: MessageSquare },
  lifecycle: { label: "Lifecycle", icon: GitBranch },
} satisfies Record<LabTab, { label: string; icon: typeof FlaskConical }>;
type ActivityOutcome = "accepted" | "running" | "passed" | "failed" | "warning";

type LabActivity = {
  id: string;
  timestamp: string;
  name: string;
  outcome: ActivityOutcome;
  projectId: string;
  documentId?: string;
  jobId?: string;
  buildId?: string;
  conversationId?: string;
  code?: string;
  requestId?: string | null;
  detail?: string;
  result?: Record<string, unknown> | null;
  tab?: LabTab;
};

type SearchRun = {
  response: SearchResponse;
  expected: string;
  passed: boolean;
  elapsedMs: number;
  buildId: string | null;
};

type MessageRun = {
  turn: ChatTurn;
  expected: string;
  passed: boolean;
  elapsedMs: number;
};

function errorFacts(error: unknown) {
  return error instanceof OperatorApiError
    ? { code: error.code, requestId: error.requestId, detail: error.message }
    : { code: "request_failed", requestId: null, detail: (error as Error).message };
}

const pipelineStages = [
  { status: "uploaded", label: "Upload" },
  { status: "parsing", label: "Parse" },
  { status: "chunking", label: "Chunk" },
  { status: "embedding", label: "Embed" },
  { status: "indexing", label: "Index" },
  { status: "ready", label: "Ready" },
] as const;

const pipelineIndex: Record<string, number> = {
  uploaded: 0,
  queued: 0,
  parsing: 1,
  chunking: 2,
  chunked: 2,
  embedding: 3,
  embedded: 3,
  indexing: 4,
  ready: 5,
  failed: -1,
  deleting: -1,
  purging: -1,
};

function pickLabDocument(documents: Document[], selectedId: string) {
  if (!documents.length) return undefined;
  const explicit = documents.find((document) => document.id === selectedId);
  if (explicit) return explicit;
  return (
    documents.find((document) => document.status === "ready") ??
    documents.find((document) => !["failed", "deleting", "purging"].includes(document.status)) ??
    documents[0]
  );
}

function filenamesMatch(typed: string, stored: string) {
  return typed.normalize("NFC") === stored.normalize("NFC");
}

function filenameLang(name: string) {
  return /[\u0980-\u09FF]/.test(name) ? "bn" : undefined;
}

function Filename({ name }: { name: string }) {
  return (
    <span className="lab-filename" dir="auto" lang={filenameLang(name)} title={name}>
      {name}
    </span>
  );
}

function sourceForDocument(sourceState: SourceState | undefined, documentId: string) {
  return sourceState?.items.find((item) => item.document_id === documentId);
}

function CopyableId({ value, label }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className="lab-id"
      title={`${label ? `${label}: ` : ""}${value}`}
      onClick={() => {
        void navigator.clipboard.writeText(value).then(() => {
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1200);
        });
      }}
    >
      <code>{shortId(value)}</code>
      {copied ? <Check size={11} aria-hidden="true" /> : <Copy size={11} aria-hidden="true" />}
      <span className="sr-only">{copied ? "Copied" : `Copy ${label ?? "id"}`}</span>
    </button>
  );
}

function CopyFilename({ filename }: { filename: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className="button button--secondary button--compact"
      onClick={() => {
        void navigator.clipboard.writeText(filename).then(() => {
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1200);
        });
      }}
    >
      {copied ? "Copied" : "Copy filename"}
    </button>
  );
}

function ApiFailure({ error }: { error: Error }) {
  const typed = error instanceof OperatorApiError ? error : null;
  return (
    <div className="failure-box lab-failure" role="alert">
      <strong>{error.message}</strong>
      <span>Code: {typed?.code ?? "request_failed"}</span>
      <span>Request ID: {typed?.requestId ?? "Not provided"}</span>
    </div>
  );
}

export function TestLab() {
  const projects = useProjects();
  const [params, setParams] = useSearchParams();
  const requestedProjectId = params.get("project") ?? "";
  const requestedTab = params.get("tab") as LabTab | null;
  const tab = requestedTab && tabs.includes(requestedTab) ? requestedTab : "journey";
  const [activities, setActivities] = useState<LabActivity[]>([]);
  const [activityOpen, setActivityOpen] = useState(false);
  const [selectedDocumentId, setSelectedDocumentId] = useState(params.get("document") ?? "");
  const [latestJobId, setLatestJobId] = useState(params.get("job") ?? "");
  const [conversationId, setConversationId] = useState(params.get("conversation") ?? "");
  const [searchRun, setSearchRun] = useState<SearchRun | null>(null);
  const [messageRun, setMessageRun] = useState<MessageRun | null>(null);

  const projectId = useMemo(() => {
    const items = projects.data?.items ?? [];
    return items.some((project) => project.id === requestedProjectId)
      ? requestedProjectId
      : (items[0]?.id ?? "");
  }, [projects.data, requestedProjectId]);

  useEffect(() => {
    if (!projectId || projectId === requestedProjectId) return;
    setParams(
      (current) => {
        current.set("project", projectId);
        if (!current.get("tab")) current.set("tab", "journey");
        return current;
      },
      { replace: true },
    );
  }, [projectId, requestedProjectId, setParams]);

  const documents = useDocuments(projectId);
  const jobs = useJobs(projectId, "", "");
  const builds = useIndexBuilds(projectId);
  const selectedProject = projects.data?.items.find((project) => project.id === projectId);
  const selectedDocument = pickLabDocument(documents.data?.items ?? [], selectedDocumentId);
  const latestJob = jobs.data?.items.find((job) => job.id === latestJobId) ?? jobs.data?.items[0];
  const activeBuild = builds.data?.items.find((build) => build.id === builds.data.active_build_id);
  const hasActiveCorpus = Boolean(activeBuild && activeBuild.chunk_count > 0);

  const addActivity = useCallback((item: Omit<LabActivity, "id" | "timestamp">) => {
    setActivities((current) => [
      { ...item, id: crypto.randomUUID(), timestamp: new Date().toISOString() },
      ...current,
    ]);
  }, []);

  useEffect(() => {
    if (!jobs.data?.items.length) return;
    setActivities((current) => {
      let changed = false;
      const next = current.map((item) => {
        if (!item.jobId) return item;
        const job = jobs.data.items.find((candidate) => candidate.id === item.jobId);
        if (!job) return item;
        const outcome: ActivityOutcome = ["queued", "running", "retry_scheduled"].includes(
          job.state,
        )
          ? "running"
          : job.state === "succeeded"
            ? "passed"
            : "failed";
        if (
          outcome === item.outcome &&
          job.failure_code === item.code &&
          job.result === item.result
        )
          return item;
        changed = true;
        return {
          ...item,
          outcome,
          code: job.failure_code ?? item.code,
          detail: job.failure_message ?? item.detail,
          result: job.result,
        };
      });
      return changed ? next : current;
    });
  }, [jobs.data]);

  const chooseTab = (next: LabTab) => {
    setParams((current) => {
      if (projectId) current.set("project", projectId);
      current.set("tab", next);
      return current;
    });
  };

  const chooseProject = (next: string) => {
    setSelectedDocumentId("");
    setLatestJobId("");
    setConversationId("");
    setSearchRun(null);
    setMessageRun(null);
    setParams((current) => {
      current.set("project", next);
      current.set("tab", tab);
      return current;
    });
  };

  if (projects.isPending) return <LoadingState label="Loading Test Lab projects" />;
  if (projects.isError)
    return <ErrorState error={projects.error} retry={() => void projects.refetch()} />;

  return (
    <div className="lab-shell">
      <LabHeader
        projects={projects.data.items}
        projectId={projectId}
        selectedProjectName={selectedProject?.name}
        document={selectedDocument}
        job={latestJob}
        activeBuildId={builds.data?.active_build_id ?? null}
        conversationId={conversationId}
        activityCount={activities.length}
        onProjectChange={chooseProject}
        onActivity={() => setActivityOpen(true)}
      />
      {projectId ? (
        <>
          <nav className="lab-tabs" aria-label="Test Lab sections">
            {tabs.map((name) => {
              const Icon = tabDetails[name].icon;
              return (
                <button
                  key={name}
                  type="button"
                  aria-current={tab === name ? "page" : undefined}
                  className={tab === name ? "lab-tab lab-tab--active" : "lab-tab"}
                  onClick={() => chooseTab(name)}
                >
                  <Icon size={15} aria-hidden="true" />
                  <span>{tabDetails[name].label}</span>
                </button>
              );
            })}
          </nav>
          {tab === "journey" && (
            <JourneyTab
              document={selectedDocument}
              latestJob={latestJob}
              searchRun={searchRun}
              messageRun={messageRun}
              builds={builds.data}
              activities={activities.filter((item) => item.projectId === projectId)}
              onNavigate={chooseTab}
            />
          )}
          {tab === "documents" && (
            <DocumentsTab
              projectId={projectId}
              documents={documents.data?.items ?? []}
              isLoading={documents.isPending}
              error={documents.error}
              selectedId={selectedDocument?.id ?? ""}
              jobs={jobs.data?.items ?? []}
              onSelect={setSelectedDocumentId}
              onJob={setLatestJobId}
              onActivity={addActivity}
            />
          )}
          {tab === "search" && (
            <SearchTab
              projectId={projectId}
              activeBuildId={builds.data?.active_build_id ?? null}
              documents={documents.data?.items ?? []}
              onRun={setSearchRun}
              onActivity={addActivity}
            />
          )}
          {tab === "messages" && (
            <MessagesTab
              projectId={projectId}
              conversationId={conversationId}
              hasActiveCorpus={hasActiveCorpus}
              onConversation={setConversationId}
              onRun={setMessageRun}
              onNavigate={chooseTab}
              onActivity={addActivity}
            />
          )}
          {tab === "lifecycle" && (
            <CorpusLifecycleActions
              projectId={projectId}
              onNotice={(notice) => {
                if (notice.jobId) setLatestJobId(notice.jobId);
                addActivity({ projectId, tab: "lifecycle", ...notice });
              }}
            />
          )}
        </>
      ) : (
        <EmptyState
          title="Select a Project from administration"
          detail="Project creation now lives in canonical Project administration. Lab remains a diagnostic surface."
        />
      )}
      <ActivityDrawer
        open={activityOpen}
        activities={activities}
        projectName={selectedProject?.name ?? "No project"}
        onClose={() => setActivityOpen(false)}
      />
    </div>
  );
}

function LabHeader({
  projects,
  projectId,
  selectedProjectName,
  document,
  job,
  activeBuildId,
  conversationId,
  activityCount,
  onProjectChange,
  onActivity,
}: {
  projects: Project[];
  projectId: string;
  selectedProjectName?: string;
  document?: Document;
  job?: Job;
  activeBuildId: string | null;
  conversationId: string;
  activityCount: number;
  onProjectChange: (id: string) => void;
  onActivity: () => void;
}) {
  return (
    <section className="panel lab-header">
      <div className="lab-header__controls">
        {projects.length > 0 && (
          <ProjectSelector projects={projects} value={projectId} onChange={onProjectChange} />
        )}
        <Link className="button button--secondary button--compact" to="/projects">
          Manage Projects
        </Link>
        <button
          className="button button--secondary button--compact lab-activity-button"
          type="button"
          onClick={onActivity}
        >
          <Activity size={15} aria-hidden="true" /> Activity
          {activityCount > 0 && <span>{activityCount}</span>}
        </button>
      </div>
      <div className="lab-session-summary">
        <SummaryFact label="Project" value={selectedProjectName ?? "None"} />
        <SummaryFact
          label="Document"
          value={document?.filename ?? "None"}
          badge={document?.status}
        />
        <SummaryFact
          label="Latest job"
          value={job ? shortId(job.id) : "None"}
          badge={job?.state}
          idValue={job?.id}
        />
        <SummaryFact label="Active build" value={activeBuildId ? shortId(activeBuildId) : "None"} />
        <SummaryFact
          label="Conversation"
          value={conversationId ? shortId(conversationId) : "None"}
        />
      </div>
    </section>
  );
}

function SummaryFact({
  label,
  value,
  badge,
  idValue,
}: {
  label: string;
  value: string;
  badge?: string;
  idValue?: string;
}) {
  return (
    <span>
      <small>{label}</small>
      <span className="lab-session-summary__value">
        {idValue ? <CopyableId value={idValue} label={label} /> : value}
        {badge && <StatusBadge status={badge} />}
      </span>
    </span>
  );
}

function JourneyTab({
  document,
  latestJob,
  searchRun,
  messageRun,
  builds,
  activities,
  onNavigate,
}: {
  document?: Document;
  latestJob?: Job;
  searchRun: SearchRun | null;
  messageRun: MessageRun | null;
  builds?: ReturnType<typeof useIndexBuilds>["data"];
  activities: LabActivity[];
  onNavigate: (tab: LabTab) => void;
}) {
  const jobActive = latestJob && ["queued", "running", "retry_scheduled"].includes(latestJob.state);
  const uploadState = document ? "passed" : "not_started";
  const processingState = !document
    ? "not_started"
    : jobActive
      ? "in_progress"
      : latestJob?.state === "failed" || document.status === "failed"
        ? "needs_attention"
        : document.status === "ready"
          ? "passed"
          : "in_progress";
  const retrievalState = searchRun ? (searchRun.passed ? "passed" : "needs_attention") : "not_started";
  const chatState = messageRun ? (messageRun.passed ? "passed" : "needs_attention") : "not_started";
  const resultsState =
    !searchRun && !messageRun
      ? "not_started"
      : searchRun?.passed === false || messageRun?.passed === false
        ? "needs_attention"
        : searchRun && messageRun
          ? "passed"
          : "in_progress";
  const lifecyclePassed = activities.some(
    (item) => item.tab === "lifecycle" && item.outcome === "passed",
  );
  const steps: Array<{
    title: string;
    detail: string;
    state: string;
    tab: LabTab;
    action: string;
  }> = [
    {
      title: "Project",
      detail: "The Lab is scoped to one ordinary project.",
      state: "passed",
      tab: "documents",
      action: "Continue to documents",
    },
    {
      title: "Upload",
      detail: document
        ? `${document.filename} is in the project.`
        : "Drop a supported file to start the pipeline.",
      state: uploadState,
      tab: "documents",
      action: document ? "Inspect document" : "Upload document",
    },
    {
      title: "Processing",
      detail: document
        ? `${document.filename} is ${document.status}${latestJob ? `; job ${shortId(latestJob.id)} is ${latestJob.state}` : ""}.`
        : "Parse, chunk, embed, and index until the document is ready.",
      state: processingState,
      tab: "documents",
      action: "Inspect processing",
    },
    {
      title: "Retrieval",
      detail: searchRun
        ? `${searchRun.response.results.length} results in ${searchRun.elapsedMs} ms${searchRun.expected ? `; expected words ${searchRun.passed ? "found" : "not found"}` : ""}.`
        : "Run a real retrieval query against the active build.",
      state: retrievalState,
      tab: "search",
      action: "Open search test",
    },
    {
      title: "Chat",
      detail: messageRun
        ? messageRun.turn.assistant_message.insufficient_evidence_reason
          ? `Valid refusal: ${messageRun.turn.assistant_message.insufficient_evidence_reason.replaceAll("_", " ")}.`
          : `${messageRun.turn.assistant_message.citations?.length ?? 0} durable citations returned.`
        : "Send a grounded message and inspect citation snapshots.",
      state: chatState,
      tab: "messages",
      action: "Open message test",
    },
    {
      title: "Results",
      detail: [searchRun, messageRun].filter(Boolean).length
        ? `${searchRun ? `${searchRun.response.results.length} hits` : "No search yet"} · ${
            messageRun
              ? `${messageRun.turn.assistant_message.citations?.length ?? 0} citations, ${messageRun.elapsedMs} ms`
              : "no chat yet"
          }.`
        : "Search hits, citations, timing, and failures collect here after you run the path.",
      state: resultsState,
      tab: messageRun ? "messages" : searchRun ? "search" : "documents",
      action: "Review evidence",
    },
  ];
  return (
    <section className="panel lab-journey">
      <div className="panel__heading">
        <div>
          <h2>End-to-end verification</h2>
          <p>Project → Upload → Processing → Retrieval → Chat → Results. Jump to any step.</p>
        </div>
      </div>
      <ol className="journey-steps">
        {steps.map((step, index) => (
          <li key={step.title}>
            <button
              className={`journey-card journey-card--${step.state}`}
              type="button"
              onClick={() => onNavigate(step.tab)}
            >
              <span className={`journey-number journey-number--${step.state}`}>
                {step.state === "passed" ? <Check aria-hidden="true" /> : index + 1}
              </span>
              <div className="journey-title">
                <h3>{step.title}</h3>
                <StatusBadge status={step.state} />
              </div>
              <p>{step.detail}</p>
              <span className="journey-card__action">
                {step.action} <ChevronRight size={14} aria-hidden="true" />
              </span>
            </button>
          </li>
        ))}
      </ol>
      <div className="journey-corpus">
        <span>
          {builds?.active_build_id
            ? `Active build is ${shortId(builds.active_build_id)}${builds.previous_build_id ? `; rollback target is ${shortId(builds.previous_build_id)}` : ""}.`
            : "No active searchable build yet."}
        </span>
        <button className="table-link" type="button" onClick={() => onNavigate("lifecycle")}>
          {lifecyclePassed ? "Corpus already refreshed" : "Open lifecycle controls"}
        </button>
      </div>
    </section>
  );
}

function DocumentsTab({
  projectId,
  documents,
  isLoading,
  error,
  selectedId,
  jobs,
  onSelect,
  onJob,
  onActivity,
}: {
  projectId: string;
  documents: Document[];
  isLoading: boolean;
  error: Error | null;
  selectedId: string;
  jobs: Job[];
  onSelect: (id: string) => void;
  onJob: (id: string) => void;
  onActivity: (item: Omit<LabActivity, "id" | "timestamp">) => void;
}) {
  const upload = useUploadDocument(projectId);
  const sourceState = useSourceState(projectId);
  const lifecycle = useDocumentLifecycleAction(projectId);
  const [dragging, setDragging] = useState(false);
  const [ocrLang, setOcrLang] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [sourceTitle, setSourceTitle] = useState("");
  const [sourceRole, setSourceRole] = useState<"primary" | "supporting" | "reference">("primary");
  const [uploadMode, setUploadMode] = useState<"independent" | "revision" | "modifies">(
    "independent",
  );
  const [uploadTarget, setUploadTarget] = useState("");
  const [purgeText, setPurgeText] = useState("");
  const [actionAccepted, setActionAccepted] = useState<{
    action: string;
    document: Document;
  } | null>(null);
  const selected = pickLabDocument(documents, selectedId);
  const selectedSource = selected
    ? sourceForDocument(sourceState.data, selected.id)
    : undefined;
  const relatedJobs = jobs
    .filter((job) => job.document_id === selected?.id || job.id === selected?.job_id)
    .slice(0, 6);
  const terminalJob = relatedJobs.find((job) => ["succeeded", "failed"].includes(job.state));
  const activeJob = relatedJobs.find((job) =>
    ["queued", "running", "retry_scheduled"].includes(job.state),
  );
  const purgeConfirmed = Boolean(selected && filenamesMatch(purgeText, selected.filename));
  const purgeBusy = lifecycle.isPending || selected?.status === "purging";
  const destructiveBusy = ["deleting", "purging"].includes(selected?.status ?? "");

  const buildSourceMetadata = (file: File): SourceRevisionCreate | undefined => {
    const target = sourceState.data?.items.find((item) => item.revision.id === uploadTarget);
    const customized =
      uploadMode !== "independent" || sourceTitle.trim() !== "" || sourceRole !== "primary";
    if (!customized) return undefined;
    if (uploadMode !== "independent" && !target) return undefined;
    return {
      activate: true,
      change_reason:
        uploadMode === "revision"
          ? "Uploaded as the latest revision of an existing source"
          : uploadMode === "modifies"
            ? "Uploaded as a modifying source"
            : "Governed Test Lab upload",
      create_new_group: uploadMode !== "revision",
      ...(uploadMode === "revision" && target
        ? { source_group_id: target.revision.source_group_id }
        : {}),
      lifecycle_status: "active",
      revision_label:
        uploadMode === "revision" && target
          ? `Revision ${target.revision.revision_number + 1}`
          : "Initial",
      source_role: sourceRole,
      title: sourceTitle.trim() || file.name,
      relationships:
        uploadMode === "independent" || !target
          ? []
          : [
              {
                relationship_type: uploadMode === "revision" ? "replaces" : "modifies",
                target_revision_id: target.revision.id,
              },
            ],
    };
  };

  const uploadFile = async (file?: File) => {
    if (!file) return;
    if (uploadMode !== "independent" && !uploadTarget) return;
    try {
      const sourceMetadata = buildSourceMetadata(file);
      const document = await upload.mutateAsync({
        file,
        ocrLang: ocrLang || undefined,
        sourceMetadata,
      });
      setSelectedFile(null);
      setSourceTitle("");
      setUploadMode("independent");
      setUploadTarget("");
      setSourceRole("primary");
      onSelect(document.id);
      if (document.job_id) onJob(document.job_id);
      onActivity({
        name: `Upload ${document.filename}`,
        outcome: "accepted",
        projectId,
        documentId: document.id,
        jobId: document.job_id ?? undefined,
        detail: "Request accepted; waiting for the processing job.",
        tab: "documents",
      });
    } catch (uploadError) {
      onActivity({
        name: `Upload ${file.name}`,
        outcome: "failed",
        projectId,
        ...errorFacts(uploadError),
        tab: "documents",
      });
    }
  };

  const runAction = async (action: "reprocess" | "embed" | "index" | "delete" | "purge") => {
    if (!selected) return;
    try {
      const document = await lifecycle.mutateAsync({
        documentId: selected.id,
        action,
        ocrLang: action === "reprocess" ? ocrLang || undefined : undefined,
      });
      setActionAccepted({ action, document });
      if (document.job_id) onJob(document.job_id);
      onActivity({
        name: `${action[0]!.toUpperCase()}${action.slice(1)} ${selected.filename}`,
        outcome: document.job_id ? "accepted" : "warning",
        projectId,
        documentId: selected.id,
        jobId: document.job_id ?? undefined,
        detail: document.job_id
          ? "Request accepted; waiting for the durable job."
          : "Backend accepted the action without returning a job identifier.",
        tab: "documents",
      });
      setPurgeText("");
    } catch (actionError) {
      onActivity({
        name: `${action} ${selected.filename}`,
        outcome: "failed",
        projectId,
        documentId: selected.id,
        ...errorFacts(actionError),
        tab: "documents",
      });
    }
  };

  if (isLoading) return <LoadingState label="Loading project documents" />;
  if (error) return <ErrorState error={error} retry={() => window.location.reload()} />;
  return (
    <div className="lab-two-column">
      <section className="panel">
        <div className="panel__heading">
          <div>
            <h2>Upload</h2>
            <p>PDF, DOCX, TXT/Markdown, PNG, JPEG, TIFF, or WebP.</p>
          </div>
        </div>
        <label
          className={`lab-dropzone${dragging ? " lab-dropzone--active" : ""}`}
          onDragEnter={() => setDragging(true)}
          onDragLeave={() => setDragging(false)}
          onDragOver={(event) => event.preventDefault()}
          onDrop={(event: DragEvent<HTMLLabelElement>) => {
            event.preventDefault();
            setDragging(false);
            setSelectedFile(event.dataTransfer.files[0] ?? null);
          }}
        >
          <UploadCloud aria-hidden="true" />
          <strong>
            {selectedFile ? <Filename name={selectedFile.name} /> : "Drop a document here"}
          </strong>
          <span>{selectedFile ? "Ready to submit" : "or choose a file"}</span>
          <input
            type="file"
            disabled={upload.isPending}
            accept=".pdf,.docx,.txt,.md,.png,.jpg,.jpeg,.tif,.tiff,.webp"
            onChange={(event) => setSelectedFile(event.target.files?.[0] ?? null)}
          />
        </label>
        <details className="lab-advanced lab-source-versioning">
          <summary>Source versioning</summary>
          <div>
            <label className="field-control">
              <span>Source treatment</span>
              <select
                aria-label="Source treatment"
                value={uploadMode}
                onChange={(event) => {
                  setUploadMode(event.target.value as typeof uploadMode);
                  setUploadTarget("");
                }}
              >
                <option value="independent">New independent source</option>
                <option value="revision">Latest revision of an existing source</option>
                <option value="modifies">Modifies an existing source</option>
              </select>
            </label>
            {uploadMode !== "independent" && (
              <label className="field-control">
                <span>Existing source</span>
                <select
                  aria-label="Existing source"
                  required
                  value={uploadTarget}
                  onChange={(event) => setUploadTarget(event.target.value)}
                >
                  <option value="">Select current source</option>
                  {(sourceState.data?.items ?? []).map((item) => (
                    <option key={item.revision.id} value={item.revision.id}>
                      {item.revision.title} · r{item.revision.revision_number} ·{" "}
                      {item.revision.source_role}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <label className="field-control">
              <span>Source role</span>
              <select
                aria-label="Source role"
                value={sourceRole}
                onChange={(event) =>
                  setSourceRole(event.target.value as typeof sourceRole)
                }
              >
                <option value="primary">Primary (authoritative / latest)</option>
                <option value="supporting">Supporting</option>
                <option value="reference">Reference</option>
              </select>
            </label>
            <label className="field-control">
              <span>Source title (optional)</span>
              <input
                aria-label="Source title"
                value={sourceTitle}
                onChange={(event) => setSourceTitle(event.target.value)}
                placeholder="Defaults to the filename"
              />
            </label>
          </div>
          <p className="lab-help">
            Processing version increments on reprocess. To mark this file as the latest edition of
            an existing source, choose “Latest revision of an existing source”. Full source
            history lives on Projects → Sources.
          </p>
        </details>
        <div className="lab-upload-submit">
          <label className="field-control lab-ocr-control">
            <span>OCR language</span>
            <select
              aria-label="OCR language"
              value={ocrLang}
              onChange={(event) => setOcrLang(event.target.value)}
            >
              <option value="">Auto / deployment default</option>
              <option value="bn">Bangla (Bengali)</option>
              <option value="en">English</option>
            </select>
          </label>
          <button
            className="button button--primary"
            type="button"
            disabled={!selectedFile || upload.isPending || (uploadMode !== "independent" && !uploadTarget)}
            onClick={() => void uploadFile(selectedFile ?? undefined)}
          >
            {upload.isPending ? "Submitting…" : "Submit document"}
          </button>
        </div>
        {upload.isSuccess && (
          <div className="notice-card lab-request-card" role="status">
            <Check aria-hidden="true" />
            <div>
              <strong>Request accepted</strong>
              <p>
                Document <Filename name={upload.data.filename} /> was accepted. Processing is not complete until job{" "}
                {upload.data.job_id ? (
                  <Link to={`/jobs?project=${projectId}&job=${upload.data.job_id}`}>
                    {shortId(upload.data.job_id)}
                  </Link>
                ) : (
                  "(identifier unavailable)"
                )}{" "}
                reaches a terminal state.
              </p>
            </div>
          </div>
        )}
        {upload.isError && <ApiFailure error={upload.error} />}
        <div className="lab-document-list-heading">
          <h3>Project documents</h3>
          <small>{documents.length} in this project</small>
        </div>
        {documents.length === 0 ? (
          <div className="inline-empty">No documents have been uploaded to this project.</div>
        ) : (
          <ul className="lab-document-list">
            {documents.map((document) => {
              const source = sourceForDocument(sourceState.data, document.id);
              return (
              <li key={document.id} className={document.id === selected?.id ? "selected" : ""}>
                <button type="button" onClick={() => onSelect(document.id)}>
                  <FileText aria-hidden="true" />
                  <span>
                    <strong>
                      <Filename name={document.filename} />
                    </strong>
                    <small>
                      processing v{document.version} · {formatBytes(document.size_bytes)}
                      {source
                        ? ` · ${source.revision.source_role} r${source.revision.revision_number}`
                        : ""}
                    </small>
                  </span>
                  <StatusBadge status={document.status} />
                </button>
              </li>
              );
            })}
          </ul>
        )}
      </section>
      <section className="panel lab-document-detail">
        <div className="panel__heading">
          <div>
            <h2>{selected ? <Filename name={selected.filename} /> : "Select a document"}</h2>
            <p>
              {selected
                ? `Processing version ${selected.version} · updated ${formatDate(selected.updated_at)}`
                : "Upload or select a document to inspect it."}
            </p>
          </div>
          {selected && <StatusBadge status={selected.status} />}
        </div>
        {selected ? (
          <div className="lab-panel-body">
            <ProcessingPipeline status={selected.status} />
            {activeJob && (
              <div className="lab-job-progress" role="status">
                <div>
                  <strong>
                    {activeJob.job_type.replaceAll(".", " ")} · {activeJob.state}
                  </strong>
                  <span>
                    Job{" "}
                    <Link to={`/jobs?project=${projectId}&job=${activeJob.id}`}>
                      {shortId(activeJob.id)}
                    </Link>
                    {typeof activeJob.progress === "number" ? ` · ${activeJob.progress}%` : ""}
                  </span>
                </div>
                {typeof activeJob.progress === "number" && (
                  <div className="progress-track" aria-hidden="true">
                    <span style={{ width: `${Math.max(4, activeJob.progress)}%` }} />
                  </div>
                )}
              </div>
            )}
            {selected.error_message && (
              <div className="failure-box">
                <strong>Processing failure</strong>
                <p>{selected.error_message}</p>
              </div>
            )}
            <details className="lab-advanced">
              <summary>Document facts and IDs</summary>
              <dl className="detail-list">
                <div>
                  <dt>Document ID</dt>
                  <dd>
                    <CopyableId value={selected.id} label="Document ID" />
                  </dd>
                </div>
                <div>
                  <dt>Processing job</dt>
                  <dd>
                    {selected.job_id ? (
                      <Link to={`/jobs?project=${projectId}&job=${selected.job_id}`}>
                        {shortId(selected.job_id)}
                      </Link>
                    ) : (
                      "See job history below"
                    )}
                  </dd>
                </div>
                <div>
                  <dt>Parser</dt>
                  <dd>{selected.accepted_parser ?? selected.parser_name ?? "—"}</dd>
                </div>
                <div>
                  <dt>Pages / language</dt>
                  <dd>
                    {selected.page_count ?? "—"} / {selected.language ?? "—"}
                  </dd>
                </div>
                <div>
                  <dt>OCR override</dt>
                  <dd>{selected.ocr_lang ?? "Auto / deployment default"}</dd>
                </div>
                <div>
                  <dt>Source role</dt>
                  <dd>
                    {selectedSource
                      ? `${selectedSource.revision.source_role} · ${selectedSource.revision.lifecycle_status} · r${selectedSource.revision.revision_number}`
                      : "Neutral defaults until source metadata is set"}
                  </dd>
                </div>
                {selectedSource && (
                  <div>
                    <dt>Source title</dt>
                    <dd>
                      <Filename name={selectedSource.revision.title} />
                    </dd>
                  </div>
                )}
              </dl>
            </details>
            <section>
              <h3>Processing actions</h3>
              <div className="button-row">
                <ActionButton
                  label="Reprocess"
                  disabled={lifecycle.isPending || destructiveBusy}
                  reason="Unavailable while deletion or purge is running."
                  onClick={() => void runAction("reprocess")}
                />
                <ActionButton
                  label="Build search index"
                  disabled={
                    lifecycle.isPending ||
                    !["chunked", "embedded", "ready"].includes(selected.status)
                  }
                  reason="Indexing requires a chunked document. Reprocess first if parsing is incomplete."
                  onClick={() => void runAction("embed")}
                />
              </div>
              <p className="lab-help">
                Build search index writes vectors and keywords together. Reprocess starts from
                parse and chunk when the source text needs to change.
              </p>
            </section>
            <section>
              <h3>Remove from corpus</h3>
              <div className="button-row">
                <ActionButton
                  label="Delete"
                  disabled={lifecycle.isPending || destructiveBusy}
                  reason="Delete is already running or the document is being purged."
                  onClick={() => void runAction("delete")}
                />
              </div>
              <p className="lab-help">
                Delete is reversible at the corpus level: retained artifacts remain available for
                rollback. Purge permanently removes the file, chunks, embeddings, and storage
                artifacts.
              </p>
            </section>
            <section className="lab-danger-zone">
              <h3>Purge permanently</h3>
              <p>
                Type the exact filename{" "}
                <strong>
                  <Filename name={selected.filename} />
                </strong>{" "}
                to enable complete deletion. Copy it first if the name uses Bangla or other
                combining characters. The backend will then remove related chunks, embeddings, and
                stored files.
              </p>
              <div className="lab-confirm-row">
                <input
                  aria-label="Purge confirmation"
                  value={purgeText}
                  onChange={(event) => setPurgeText(event.target.value)}
                  placeholder="Type filename to enable Purge"
                  dir="auto"
                  lang={filenameLang(selected.filename)}
                  spellCheck={false}
                  autoComplete="off"
                />
                <CopyFilename filename={selected.filename} />
                <button
                  className="danger-button"
                  type="button"
                  disabled={!purgeConfirmed || purgeBusy}
                  title={
                    selected.status === "purging"
                      ? "Purge is already running."
                      : lifecycle.isPending
                        ? "Another lifecycle action is running."
                        : purgeConfirmed
                          ? "Irreversibly purge this document and every retained artifact."
                          : "Type the exact filename to enable irreversible purge."
                  }
                  onClick={() => void runAction("purge")}
                >
                  Purge
                </button>
              </div>
              <small
                className={
                  purgeConfirmed ? "lab-confirm-hint lab-confirm-hint--ready" : "lab-confirm-hint"
                }
              >
                {selected.status === "purging"
                  ? "Purge is already running."
                  : purgeConfirmed
                    ? "Filename confirmed. This cannot be undone."
                    : "Type the exact filename to enable Purge. This permanently deletes the document, embeddings, and stored files."}
              </small>
            </section>
            {lifecycle.isError && <ApiFailure error={lifecycle.error} />}
            {actionAccepted && (
              <div className="notice-card lab-request-card" role="status">
                <Check aria-hidden="true" />
                <div>
                  <strong>Request accepted</strong>
                  <p>
                    {actionAccepted.action} was accepted. Processing is not complete until job{" "}
                    {actionAccepted.document.job_id ? (
                      <Link to={`/jobs?project=${projectId}&job=${actionAccepted.document.job_id}`}>
                        {shortId(actionAccepted.document.job_id)}
                      </Link>
                    ) : (
                      "(identifier unavailable)"
                    )}{" "}
                    reaches a terminal state.
                  </p>
                </div>
              </div>
            )}
            {terminalJob && !activeJob && (
              <div
                className={`lab-verification ${terminalJob.state === "succeeded" ? "lab-verification--pass" : "lab-verification--warning"}`}
              >
                <StatusBadge status={terminalJob.state} />
                <strong>
                  {terminalJob.state === "succeeded"
                    ? "Processing finished successfully"
                    : "Processing job failed"}
                </strong>
                <span>
                  Job{" "}
                  <Link to={`/jobs?project=${projectId}&job=${terminalJob.id}`}>
                    {terminalJob.id}
                  </Link>
                  {terminalJob.failure_code
                    ? ` · ${terminalJob.failure_code}: ${terminalJob.failure_message}`
                    : " reached a terminal state."}
                </span>
              </div>
            )}
            <details className="lab-advanced" open={Boolean(relatedJobs.length)}>
              <summary>Related durable jobs</summary>
              {relatedJobs.length ? (
                <ul className="lab-job-list">
                  {relatedJobs.map((job) => (
                    <li key={job.id}>
                      <Link to={`/jobs?project=${projectId}&job=${job.id}`}>
                        {job.job_type} · {shortId(job.id)}
                      </Link>
                      <StatusBadge status={job.state} />
                      {job.failure_code && (
                        <small>
                          {job.failure_code}: {job.failure_message}
                        </small>
                      )}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="lab-help">No related jobs are visible yet.</p>
              )}
            </details>
          </div>
        ) : (
          <div className="inline-empty">No document selected.</div>
        )}
      </section>
    </div>
  );
}

function ProcessingPipeline({ status }: { status: string }) {
  const activeIndex = pipelineIndex[status] ?? 0;
  const failed = status === "failed";
  return (
    <ol className="lab-pipeline" aria-label="Document processing pipeline">
      {pipelineStages.map((stage, index) => {
        const completed = !failed && (activeIndex > index || status === "ready");
        const active = !failed && activeIndex === index && status !== "ready";
        return (
          <li
            key={stage.status}
            className={failed ? "failed" : completed ? "completed" : active ? "active" : "pending"}
          >
            <span>{completed ? <Check size={11} aria-hidden="true" /> : index + 1}</span>
            <small>{stage.label}</small>
          </li>
        );
      })}
    </ol>
  );
}

function ActionButton({
  label,
  disabled,
  reason,
  onClick,
}: {
  label: string;
  disabled: boolean;
  reason: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      title={disabled ? reason : undefined}
      onClick={onClick}
    >
      {label}
    </button>
  );
}

function SearchTab({
  projectId,
  activeBuildId,
  documents,
  onRun,
  onActivity,
}: {
  projectId: string;
  activeBuildId: string | null;
  documents: Document[];
  onRun: (run: SearchRun) => void;
  onActivity: (item: Omit<LabActivity, "id" | "timestamp">) => void;
}) {
  const search = useSearch(projectId);
  const [query, setQuery] = useState("");
  const [expected, setExpected] = useState("");
  const [documentId, setDocumentId] = useState("");
  const [strategy, setStrategy] = useState<"" | "semantic" | "hybrid">("");
  const [run, setRun] = useState<SearchRun | null>(null);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const started = performance.now();
    try {
      const response = await search.mutateAsync({
        query,
        document_id: documentId || null,
        strategy: strategy || null,
      });
      const words = expected.trim().toLocaleLowerCase();
      const passed =
        response.results.length > 0 &&
        (!words ||
          response.results.some((result) => result.content.toLocaleLowerCase().includes(words)));
      const next = {
        response,
        expected,
        passed,
        elapsedMs: Math.round(performance.now() - started),
        buildId: activeBuildId,
      };
      setRun(next);
      onRun(next);
      onActivity({
        name: `Search: ${query}`,
        outcome: passed ? "passed" : "warning",
        projectId,
        buildId: activeBuildId ?? undefined,
        detail: `${response.results.length} results; ${next.elapsedMs} ms.`,
        tab: "search",
      });
    } catch (searchError) {
      onActivity({
        name: `Search: ${query}`,
        outcome: "failed",
        projectId,
        buildId: activeBuildId ?? undefined,
        ...errorFacts(searchError),
        tab: "search",
      });
    }
  };
  return (
    <section className="panel lab-focused-panel">
      <div className="panel__heading">
        <div>
          <h2>Retrieval test</h2>
          <p>Query the active immutable build and inspect ranked chunks, scores, and timing.</p>
        </div>
      </div>
      <form className="lab-test-form lab-test-form--search" onSubmit={(event) => void submit(event)}>
        <label className="field-control field-control--grow">
          <span>Query</span>
          <textarea
            required
            rows={4}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="What phrase should this document contain?"
          />
        </label>
        <div className="lab-search-side">
          <label className="field-control">
            <span>Expected words (optional)</span>
            <input
              value={expected}
              onChange={(event) => setExpected(event.target.value)}
              placeholder="exact words"
            />
          </label>
          <button className="button button--primary" type="submit" disabled={search.isPending}>
            <Search size={16} aria-hidden="true" />
            {search.isPending ? "Searching…" : "Search"}
          </button>
        </div>
        <details className="lab-advanced">
          <summary>Advanced filters</summary>
          <div>
            <label className="field-control">
              <span>Document</span>
              <select value={documentId} onChange={(event) => setDocumentId(event.target.value)}>
                <option value="">All ready documents</option>
                {documents.map((document) => (
                  <option key={document.id} value={document.id}>
                    {document.filename}
                  </option>
                ))}
              </select>
            </label>
            <label className="field-control">
              <span>Strategy</span>
              <select
                value={strategy}
                onChange={(event) => setStrategy(event.target.value as typeof strategy)}
              >
                <option value="">Deployment default</option>
                <option value="hybrid">Hybrid</option>
                <option value="semantic">Semantic</option>
              </select>
            </label>
          </div>
        </details>
      </form>
      {search.isError && (
        <div className="lab-panel-body">
          <ApiFailure error={search.error} />
        </div>
      )}
      {run && <SearchResults run={run} projectId={projectId} />}
      {!run && !search.isPending && (
        <div className="inline-empty">Enter a query to inspect ranked retrieval results.</div>
      )}
    </section>
  );
}

function SearchResults({ run, projectId }: { run: SearchRun; projectId: string }) {
  return (
    <div className="lab-results">
      <div
        className={`lab-verification ${run.passed ? "lab-verification--pass" : "lab-verification--warning"}`}
      >
        <StatusBadge status={run.passed ? "passed" : "needs_attention"} />
        <strong>
          {run.response.results.length
            ? `${run.response.results.length} results returned`
            : "No results"}
        </strong>
        <span>
          {formatDuration(run.elapsedMs)} client ·{" "}
          {formatDuration(run.response.diagnostics?.duration_ms)} backend · active build{" "}
          {run.buildId ? shortId(run.buildId) : "none"}
          {run.response.diagnostics?.strategy ? ` · ${run.response.diagnostics.strategy}` : ""}
        </span>
        {run.expected && (
          <span>
            Expected words “{run.expected}” were {run.passed ? "found" : "not found"}.
          </span>
        )}
      </div>
      {run.response.diagnostics && (
        <details className="lab-advanced lab-results-advanced">
          <summary>Retrieval diagnostics</summary>
          <dl className="detail-list">
            <div>
              <dt>Strategy</dt>
              <dd>{run.response.diagnostics.strategy}</dd>
            </div>
            <div>
              <dt>Rerank</dt>
              <dd>
                {run.response.diagnostics.rerank_status}
                {run.response.diagnostics.reranker_model
                  ? ` · ${run.response.diagnostics.reranker_model}`
                  : ""}
              </dd>
            </div>
            <div>
              <dt>Duplicates removed</dt>
              <dd>{run.response.diagnostics.duplicate_suppression_removed_count}</dd>
            </div>
            {run.response.diagnostics.index_build_id && (
              <div>
                <dt>Index build</dt>
                <dd>
                  <CopyableId
                    value={run.response.diagnostics.index_build_id}
                    label="Index build"
                  />
                </dd>
              </div>
            )}
          </dl>
        </details>
      )}
      {run.response.results.length === 0 ? (
        <div className="inline-empty">
          The backend returned no matching chunks. This is a valid search outcome.
        </div>
      ) : (
        <ol className="search-result-list">
          {run.response.results.map((result, index) => (
            <li key={result.chunk_id}>
              <div className="search-result-heading">
                <span className="search-rank">#{index + 1}</span>
                <div>
                  <strong>
                    <Filename name={result.filename} />
                  </strong>
                  <small>
                    Page {result.page_number ?? "—"} · chunk {result.chunk_index} · score{" "}
                    {result.score.toFixed(4)}
                  </small>
                </div>
                <Link to={`/lab?project=${projectId}&tab=documents&document=${result.document_id}`}>
                  Document
                </Link>
              </div>
              <p>{result.content}</p>
              <details className="lab-advanced">
                <summary>
                  Chunk {shortId(result.chunk_id)} · chars {result.char_start ?? "—"}–
                  {result.char_end ?? "—"}
                </summary>
                <dl className="detail-list">
                  <div>
                    <dt>Chunk ID</dt>
                    <dd>
                      <CopyableId value={result.chunk_id} label="Chunk ID" />
                    </dd>
                  </div>
                  <div>
                    <dt>Document ID</dt>
                    <dd>
                      <CopyableId value={result.document_id} label="Document ID" />
                    </dd>
                  </div>
                  <div>
                    <dt>Source offsets</dt>
                    <dd>
                      {result.char_start ?? "—"}–{result.char_end ?? "—"}
                    </dd>
                  </div>
                </dl>
              </details>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function MessagesTab({
  projectId,
  conversationId,
  hasActiveCorpus,
  onConversation,
  onRun,
  onNavigate,
  onActivity,
}: {
  projectId: string;
  conversationId: string;
  hasActiveCorpus: boolean;
  onConversation: (id: string) => void;
  onRun: (run: MessageRun) => void;
  onNavigate: (tab: LabTab) => void;
  onActivity: (item: Omit<LabActivity, "id" | "timestamp">) => void;
}) {
  const create = useCreateConversation(projectId);
  const messages = useMessages(projectId, conversationId);
  const send = useSendMessage(projectId, conversationId);
  const stream = useStreamMessage(projectId, conversationId);
  const [content, setContent] = useState("");
  const [expected, setExpected] = useState("");
  const [delivery, setDelivery] = useState<"regular" | "stream">("regular");
  const [streamedContent, setStreamedContent] = useState("");
  const historyRef = useRef<HTMLDivElement>(null);
  const [lastRun, setLastRun] = useState<MessageRun | null>(null);
  const [selectedAssistantId, setSelectedAssistantId] = useState("");
  const [activeCitation, setActiveCitation] = useState(0);
  const inspectAssistant = (id: string, citationIndex = 0) => {
    setSelectedAssistantId(id);
    setActiveCitation(citationIndex);
  };
  const newConversation = async () => {
    try {
      const conversation = await create.mutateAsync(`Test Lab ${new Date().toLocaleString()}`);
      onConversation(conversation.id);
      setLastRun(null);
      onActivity({
        name: "New test conversation",
        outcome: "passed",
        projectId,
        conversationId: conversation.id,
        detail: conversation.title ?? "Untitled test conversation",
        tab: "messages",
      });
    } catch (error) {
      onActivity({
        name: "New test conversation",
        outcome: "failed",
        projectId,
        ...errorFacts(error),
        tab: "messages",
      });
    }
  };
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const submittedContent = content;
    const started = performance.now();
    try {
      setStreamedContent("");
      const turn =
        delivery === "stream"
          ? await stream.mutateAsync({
              content: submittedContent,
              onDelta: (delta) => setStreamedContent((current) => current + delta),
            })
          : await send.mutateAsync({ content: submittedContent });
      const assistant = turn.assistant_message;
      const refusal = Boolean(assistant.insufficient_evidence_reason);
      const hasCitations = Boolean(assistant.citations?.length);
      const expectedMatches =
        !expected.trim() ||
        assistant.content.toLocaleLowerCase().includes(expected.trim().toLocaleLowerCase());
      const passed = expectedMatches && (refusal || (assistant.grounded === true && hasCitations));
      const next = { turn, expected, passed, elapsedMs: Math.round(performance.now() - started) };
      setLastRun(next);
      setSelectedAssistantId(turn.assistant_message.id);
      setActiveCitation(0);
      onRun(next);
      setContent("");
      setStreamedContent("");
      onActivity({
        name: "Grounded message",
        outcome: passed ? "passed" : "warning",
        projectId,
        conversationId,
        detail: refusal
          ? `Valid refusal: ${assistant.insufficient_evidence_reason}`
          : `${assistant.citations?.length ?? 0} citations; ${next.elapsedMs} ms.`,
        tab: "messages",
      });
    } catch (error) {
      setStreamedContent("");
      onActivity({
        name: "Grounded message",
        outcome: "failed",
        projectId,
        conversationId,
        ...errorFacts(error),
        tab: "messages",
      });
    }
  };
  const history = messages.data?.items ?? [];
  const visibleMessages =
    lastRun && !history.some((message) => message.id === lastRun.turn.assistant_message.id)
      ? [
          ...history,
          ...[lastRun.turn.user_message, lastRun.turn.assistant_message].filter(
            (message) => !history.some((item) => item.id === message.id),
          ),
        ]
      : history;
  const assistantMessages = visibleMessages.filter((message) => message.role === "assistant");
  const inspectedMessage =
    assistantMessages.find((message) => message.id === selectedAssistantId) ??
    assistantMessages.at(-1) ??
    null;
  useEffect(() => {
    const historyElement = historyRef.current;
    if (historyElement) {
      historyElement.scrollTop = historyElement.scrollHeight;
    }
  }, [conversationId, streamedContent, visibleMessages.length]);
  return (
    <section className="panel lab-chat-shell">
      <div className="lab-chat-topbar">
        <div>
          <p className="eyebrow">Grounded chat</p>
          <h2>Ask the corpus</h2>
        </div>
        <div className="lab-chat-topbar__actions">
          {conversationId && <CopyableId value={conversationId} label="Conversation ID" />}
          <button
            className="button button--secondary button--compact"
            type="button"
            onClick={() => void newConversation()}
            disabled={create.isPending}
          >
            <Plus size={14} aria-hidden="true" />
            New test conversation
          </button>
        </div>
      </div>
      {!hasActiveCorpus && (
        <div className="degraded-banner lab-chat-banner">
          <FlaskConical aria-hidden="true" />
          <div>
            <strong>No active searchable corpus</strong>
            <p>
              Process and index a document, or activate a validated build.{" "}
              <button className="table-link" type="button" onClick={() => onNavigate("documents")}>
                Documents
              </button>{" "}
              ·{" "}
              <button className="table-link" type="button" onClick={() => onNavigate("lifecycle")}>
                Lifecycle
              </button>
            </p>
          </div>
        </div>
      )}
      {!conversationId ? (
        <div className="query-state lab-conversation-empty">
          <span className="lab-chat-mark" aria-hidden="true">
            <Sparkles size={22} />
          </span>
          <h2>No test conversation</h2>
          <p>
            Create one on demand for this project. Existing product conversations are not reused
            automatically.
          </p>
          <button
            className="button button--primary"
            type="button"
            onClick={() => void newConversation()}
            disabled={create.isPending}
          >
            New test conversation
          </button>
          {create.isError && <ApiFailure error={create.error} />}
        </div>
      ) : (
        <>
          <div className="lab-message-workspace">
            <section className="lab-chat-pane" aria-label="Test conversation">
              <div
                ref={historyRef}
                className={`message-history${visibleMessages.length ? "" : " message-history--idle"}`}
                aria-live="polite"
              >
                {messages.isPending ? (
                  <span className="spinner" />
                ) : messages.isError ? (
                  <ApiFailure error={messages.error} />
                ) : visibleMessages.length ? (
                  visibleMessages.map((message) => (
                    <MessageCard
                      key={message.id}
                      message={message}
                      selected={message.id === inspectedMessage?.id}
                      activeCitation={
                        message.id === inspectedMessage?.id ? activeCitation : undefined
                      }
                      onInspect={
                        message.role === "assistant"
                          ? (citationIndex) => inspectAssistant(message.id, citationIndex)
                          : undefined
                      }
                    />
                  ))
                ) : (
                  <div className="lab-chat-idle">
                    <span className="lab-chat-mark" aria-hidden="true">
                      <MessageSquare size={22} />
                    </span>
                    <p>No messages yet. Ask one focused validation question.</p>
                    <div className="lab-prompt-chips">
                      {[
                        "What is this document about?",
                        "Quote the key requirement or policy.",
                      ].map((prompt) => (
                        <button
                          key={prompt}
                          type="button"
                          onClick={() => setContent(prompt)}
                        >
                          {prompt}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
                {stream.isPending && (
                  <article className="message-card message-card--assistant message-card--streaming">
                    <div>
                      <strong>Grounded response</strong>
                      <span className="lab-streaming-status">Streaming</span>
                    </div>
                    <p>{streamedContent || "Preparing grounded response…"}</p>
                    {!streamedContent && (
                      <span className="lab-typing" aria-hidden="true">
                        <i />
                        <i />
                        <i />
                      </span>
                    )}
                  </article>
                )}
              </div>
              <form
                className="lab-message-form lab-message-form--chat"
                onSubmit={(event) => void submit(event)}
              >
                <div className="lab-composer">
                  <textarea
                    aria-label="Message"
                    required
                    rows={3}
                    value={content}
                    onChange={(event) => setContent(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" && !event.shiftKey) {
                        event.preventDefault();
                        event.currentTarget.form?.requestSubmit();
                      }
                    }}
                    placeholder="Ask a question grounded in the active corpus…"
                  />
                  <div className="lab-composer__toolbar">
                    <div className="lab-mode-switch" role="group" aria-label="Reply mode">
                      {(["regular", "stream"] as const).map((mode) => (
                        <button
                          key={mode}
                          type="button"
                          className={delivery === mode ? "is-active" : undefined}
                          disabled={send.isPending || stream.isPending}
                          onClick={() => setDelivery(mode)}
                        >
                          {mode === "regular" ? "Regular" : "Stream live"}
                        </button>
                      ))}
                    </div>
                    <label className="lab-expected-answer">
                      <span>Expected answer words</span>
                      <input
                        value={expected}
                        onChange={(event) => setExpected(event.target.value)}
                        placeholder="Optional check"
                      />
                    </label>
                    <span className="lab-composer__hint">Enter sends</span>
                    <button
                      className="lab-composer__send"
                      type="submit"
                      aria-label="Send message"
                      disabled={send.isPending || stream.isPending || !hasActiveCorpus}
                    >
                      <Send size={16} aria-hidden="true" />
                      <span>{send.isPending || stream.isPending ? "Sending" : "Send"}</span>
                    </button>
                  </div>
                </div>
              </form>
            </section>
            <MessageInspector
              message={inspectedMessage}
              run={lastRun}
              activeCitation={activeCitation}
              onCite={setActiveCitation}
            />
          </div>
          {(send.isError || stream.isError) && (
            <div className="lab-panel-body">
              <ApiFailure error={(send.error ?? stream.error) as Error} />
            </div>
          )}
        </>
      )}
    </section>
  );
}

function MessageCard({
  message,
  selected = false,
  activeCitation,
  onInspect,
}: {
  message: Message;
  selected?: boolean;
  activeCitation?: number;
  onInspect?: (citationIndex?: number) => void;
}) {
  const refusal = message.insufficient_evidence_reason;
  const citations = message.citations ?? [];
  return (
    <article
      className={`message-card message-card--${message.role}${selected ? " message-card--selected" : ""}`}
    >
      <div>
        <strong>{message.role === "assistant" ? "Grounded response" : "You"}</strong>
        <time>{formatDate(message.created_at)}</time>
      </div>
      <p>{message.content}</p>
      {message.role === "assistant" && (
        <div className="message-card__meta">
          {citations.length > 0 && (
            <div className="cite-chips" aria-label="Citations">
              {citations.slice(0, 5).map((citation, index) => (
                <button
                  key={`${citation.chunk_id}-${index}`}
                  type="button"
                  className={activeCitation === index ? "is-active" : undefined}
                  title={citation.filename}
                  onClick={() => onInspect?.(index)}
                >
                  {index + 1}
                </button>
              ))}
            </div>
          )}
          <button className="message-card__inspect" type="button" onClick={() => onInspect?.(0)}>
            {refusal
              ? "View refusal details"
              : `${citations.length} citation${citations.length === 1 ? "" : "s"} · view evidence`}
          </button>
        </div>
      )}
    </article>
  );
}

function MessageInspector({
  message,
  run,
  activeCitation = 0,
  onCite,
}: {
  message: Message | null;
  run: MessageRun | null;
  activeCitation?: number;
  onCite?: (index: number) => void;
}) {
  const isLatestRun = Boolean(run && message?.id === run.turn.assistant_message.id);
  if (!message) {
    return (
      <aside className="lab-message-inspector">
        <div className="lab-message-inspector__heading">
          <p className="eyebrow">Sources</p>
          <h3>Select an answer</h3>
        </div>
        <div className="lab-inspector-idle">
          <Quote size={18} aria-hidden="true" />
          <p>Grounding checks, response timing, and source citations appear here.</p>
        </div>
      </aside>
    );
  }
  const refusal = message.insufficient_evidence_reason;
  const citations = message.citations ?? [];
  const focused = citations[activeCitation] ?? citations[0];
  return (
    <aside className="lab-message-inspector" aria-label="Grounding details">
      <div className="lab-message-inspector__heading">
        <div>
          <p className="eyebrow">Sources</p>
          <h3>{refusal ? "Valid refusal" : "Grounded answer"}</h3>
        </div>
        <StatusBadge
          status={
            refusal || (message.grounded === true && citations.length)
              ? "passed"
              : "needs_attention"
          }
        />
      </div>
      {isLatestRun && run && (
        <div
          className={`lab-verification ${run.passed ? "lab-verification--pass" : "lab-verification--warning"}`}
        >
          <StatusBadge status={run.passed ? "passed" : "needs_attention"} />
          <strong>
            {message.insufficient_evidence_reason
              ? "Valid refusal / insufficient evidence"
              : message.citations?.length
                ? "Answer with citations"
                : "Answer is not verifiably grounded"}
          </strong>
          <span>
            {run.elapsedMs} ms round trip
            {run.expected ? ` · expected words ${run.passed ? "matched" : "did not match"}` : ""}
          </span>
        </div>
      )}
      <div className="lab-inspector-metrics">
        <span>
          <Clock size={13} aria-hidden="true" />
          {isLatestRun && run ? `${run.elapsedMs} ms` : "—"}
        </span>
        <span>
          <Sparkles size={13} aria-hidden="true" />
          {refusal ? "Refusal" : message.grounded ? "Grounded" : citations.length ? "Cited" : "Ungrounded"}
        </span>
        <span>
          <Quote size={13} aria-hidden="true" />
          {citations.length} sources
        </span>
      </div>
      {refusal ? (
        <div className="notice-card">
          <strong>Insufficient evidence</strong>
          <p>{refusal.replaceAll("_", " ")}</p>
        </div>
      ) : citations.length ? (
        <ol className="citation-list" aria-label={`${citations.length} citations`}>
          {citations.slice(0, 5).map((citation, index) => (
            <li
              key={`${citation.chunk_id}-${index}`}
              className={focused?.chunk_id === citation.chunk_id && index === activeCitation ? "is-active" : undefined}
            >
              <button type="button" onClick={() => onCite?.(index)}>
                <strong>
                  [{index + 1}] <Filename name={citation.filename} />
                </strong>
                <span>
                  Page {citation.page_number ?? "—"} · chunk {citation.chunk_index} · score{" "}
                  {citation.score.toFixed(4)}
                </span>
                <span
                  className="cite-score"
                  aria-hidden="true"
                  style={{ ["--score" as string]: `${Math.round(Math.min(1, Math.max(0, citation.score)) * 100)}%` }}
                >
                  <i />
                </span>
                <p>{citation.excerpt ?? `Stable chunk reference ${citation.chunk_id}`}</p>
              </button>
              <CopyableId value={citation.chunk_id} label="Citation chunk ID" />
            </li>
          ))}
        </ol>
      ) : (
        <div className="failure-box">
          <strong>No valid citations returned</strong>
          <p>Non-empty answer text alone does not pass grounding verification.</p>
        </div>
      )}
    </aside>
  );
}

function ActivityDrawer({
  open,
  activities,
  projectName,
  onClose,
}: {
  open: boolean;
  activities: LabActivity[];
  projectName: string;
  onClose: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const summary = [
    `Test Lab: ${projectName}`,
    ...activities.map(
      (item) =>
        `${item.timestamp} | ${item.outcome} | ${item.name}${item.jobId ? ` | job ${item.jobId}` : ""}${item.code ? ` | ${item.code}` : ""}${item.requestId ? ` | request ${item.requestId}` : ""}`,
    ),
  ].join("\n");
  if (!open) return null;
  return (
    <>
      <button
        className="lab-drawer-scrim"
        aria-label="Close activity"
        type="button"
        onClick={onClose}
      />
      <aside className="lab-activity-drawer" aria-label="Test Lab activity">
        <header>
          <div>
            <p className="eyebrow">Current Lab session</p>
            <h2>Activity</h2>
          </div>
          <button
            className="icon-button"
            type="button"
            onClick={onClose}
            aria-label="Close activity"
          >
            <X aria-hidden="true" />
          </button>
        </header>
        <button
          className="button button--secondary button--full"
          type="button"
          onClick={() => void navigator.clipboard.writeText(summary).then(() => setCopied(true))}
        >
          <Clipboard size={15} aria-hidden="true" />
          {copied ? "Copied summary" : "Copy compact test summary"}
        </button>
        {activities.length === 0 ? (
          <div className="inline-empty">Actions from this browser session appear here.</div>
        ) : (
          <ol className="activity-timeline">
            {activities.map((item) => (
              <li key={item.id}>
                <StatusBadge status={item.outcome} />
                <div>
                  <strong>{item.name}</strong>
                  <time>{formatDate(item.timestamp)}</time>
                  {item.detail && <p>{item.detail}</p>}
                  <div className="activity-links">
                    {item.jobId && (
                      <Link to={`/jobs?project=${item.projectId}&job=${item.jobId}`}>Job</Link>
                    )}
                    {item.documentId && (
                      <Link
                        to={`/lab?project=${item.projectId}&tab=documents&document=${item.documentId}`}
                      >
                        Document
                      </Link>
                    )}
                    {item.buildId && (
                      <Link to={`/lab?project=${item.projectId}&tab=lifecycle`}>Build</Link>
                    )}
                    <Link to="/audit">Audit</Link>
                  </div>
                  <details>
                    <summary>Technical details</summary>
                    <dl className="detail-list">
                      <div>
                        <dt>Project</dt>
                        <dd>{item.projectId}</dd>
                      </div>
                      {item.documentId && (
                        <div>
                          <dt>Document</dt>
                          <dd>{item.documentId}</dd>
                        </div>
                      )}
                      {item.jobId && (
                        <div>
                          <dt>Job</dt>
                          <dd>{item.jobId}</dd>
                        </div>
                      )}
                      {item.buildId && (
                        <div>
                          <dt>Build</dt>
                          <dd>{item.buildId}</dd>
                        </div>
                      )}
                      {item.conversationId && (
                        <div>
                          <dt>Conversation</dt>
                          <dd>{item.conversationId}</dd>
                        </div>
                      )}
                      {item.code && (
                        <div>
                          <dt>Error code</dt>
                          <dd>{item.code}</dd>
                        </div>
                      )}
                      {item.requestId && (
                        <div>
                          <dt>Request ID</dt>
                          <dd>{item.requestId}</dd>
                        </div>
                      )}
                    </dl>
                    {item.result && (
                      <pre className="json-view">{JSON.stringify(item.result, null, 2)}</pre>
                    )}
                  </details>
                </div>
              </li>
            ))}
          </ol>
        )}
      </aside>
    </>
  );
}
