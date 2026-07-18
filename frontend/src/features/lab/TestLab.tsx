import {
  Activity,
  Check,
  ChevronRight,
  Clipboard,
  FileText,
  FlaskConical,
  GitBranch,
  Menu,
  MessageSquare,
  Search,
  Send,
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
} from "../../api/operatorApiClient";
import {
  useCreateConversation,
  useCreateProject,
  useDocumentLifecycleAction,
  useDocuments,
  useIndexBuilds,
  useJobs,
  useMessages,
  useProjects,
  useSearch,
  useSendMessage,
  useStreamMessage,
  useUploadDocument,
} from "../../api/operatorConsoleQueries";
import { EmptyState, ErrorState, LoadingState } from "../../components/QueryStatePanel";
import { ProjectSelector } from "../../components/ProjectSelector";
import { StatusBadge } from "../../components/StatusBadge";
import { formatBytes, formatDate, shortId } from "../../shared/formatters";
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
  traceId?: string | null;
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
    ? { code: error.code, traceId: error.traceId, detail: error.message }
    : { code: "request_failed", traceId: null, detail: (error as Error).message };
}

function ApiFailure({ error }: { error: Error }) {
  const typed = error instanceof OperatorApiError ? error : null;
  return (
    <div className="failure-box lab-failure" role="alert">
      <strong>{error.message}</strong>
      <span>Code: {typed?.code ?? "request_failed"}</span>
      <span>Trace ID: {typed?.traceId ?? "Not provided"}</span>
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
  const selectedDocument =
    documents.data?.items.find((document) => document.id === selectedDocumentId) ??
    documents.data?.items[0];
  const latestJob = jobs.data?.items.find((job) => job.id === latestJobId) ?? jobs.data?.items[0];

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
        onProjectCreated={chooseProject}
        onActivity={() => setActivityOpen(true)}
        onActivityRecord={addActivity}
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
              hasActiveCorpus={Boolean(
                builds.data?.active_build_id &&
                documents.data?.items.some((document) => document.status === "ready"),
              )}
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
          title="Create a test project"
          detail="The Test Lab needs one ordinary project before it can run the end-to-end journey."
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
  onProjectCreated,
  onActivity,
  onActivityRecord,
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
  onProjectCreated: (id: string) => void;
  onActivity: () => void;
  onActivityRecord: (item: Omit<LabActivity, "id" | "timestamp">) => void;
}) {
  const createProject = useCreateProject();
  const [creating, setCreating] = useState(projects.length === 0);
  const [name, setName] = useState("");
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    try {
      const project = await createProject.mutateAsync({
        name,
        description: "Browser Test Lab project",
      });
      onActivityRecord({
        name: "Created test project",
        outcome: "passed",
        projectId: project.id,
        detail: project.name,
        tab: "journey",
      });
      setName("");
      setCreating(false);
      onProjectCreated(project.id);
    } catch (error) {
      onActivityRecord({
        name: "Create test project",
        outcome: "failed",
        projectId: projectId || "not-created",
        ...errorFacts(error),
        tab: "journey",
      });
    }
  };
  return (
    <section className="panel lab-header">
      <div className="lab-header__controls">
        {projects.length > 0 && (
          <ProjectSelector projects={projects} value={projectId} onChange={onProjectChange} />
        )}
        <button
          className="button button--secondary"
          type="button"
          onClick={() => setCreating(true)}
        >
          Create test project
        </button>
        <button
          className="button button--secondary lab-activity-button"
          type="button"
          onClick={onActivity}
        >
          <Activity size={16} aria-hidden="true" /> Activity
          {activityCount > 0 && <span>{activityCount}</span>}
        </button>
      </div>
      {creating && (
        <form className="lab-create-project" onSubmit={(event) => void submit(event)}>
          <label className="field-control field-control--grow">
            <span>Test project name</span>
            <input
              autoFocus
              required
              maxLength={255}
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Validation – July 19"
            />
          </label>
          <button
            className="button button--primary"
            type="submit"
            disabled={createProject.isPending}
          >
            {createProject.isPending ? "Creating…" : "Create project"}
          </button>
          {projects.length > 0 && (
            <button
              className="button button--secondary"
              type="button"
              onClick={() => setCreating(false)}
            >
              Cancel
            </button>
          )}
          {createProject.isError && <ApiFailure error={createProject.error} />}
        </form>
      )}
      <div className="lab-session-summary">
        <strong>{selectedProjectName ?? "No project selected"}</strong>
        <SummaryFact label="Document" value={document?.filename ?? "Not selected"} />
        <SummaryFact
          label="Latest job"
          value={job ? `${shortId(job.id)} · ${job.state}` : "None"}
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

function SummaryFact({ label, value }: { label: string; value: string }) {
  return (
    <span>
      <small>{label}</small>
      {value}
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
  const documentState = !document
    ? "not_started"
    : jobActive
      ? "in_progress"
      : latestJob?.state === "failed" || document.status === "failed"
        ? "needs_attention"
        : document.status === "ready"
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
      title: "Select project",
      detail: "The Lab is scoped to one ordinary project.",
      state: "passed",
      tab: "documents",
      action: "Continue to documents",
    },
    {
      title: "Upload and process document",
      detail: document
        ? `${document.filename} is ${document.status}${latestJob ? `; job ${shortId(latestJob.id)} is ${latestJob.state}` : ""}.`
        : "Upload a supported file and wait for its durable job to finish.",
      state: documentState,
      tab: "documents",
      action: document ? "Inspect document" : "Upload document",
    },
    {
      title: "Verify search",
      detail: searchRun
        ? `${searchRun.response.results.length} results in ${searchRun.elapsedMs} ms${searchRun.expected ? `; expected words ${searchRun.passed ? "found" : "not found"}.` : "."}`
        : "Run a real retrieval query against the active build.",
      state: searchRun ? (searchRun.passed ? "passed" : "needs_attention") : "not_started",
      tab: "search",
      action: "Open search test",
    },
    {
      title: "Verify message and citations",
      detail: messageRun
        ? messageRun.turn.assistant_message.insufficient_evidence_reason
          ? `Valid refusal: ${messageRun.turn.assistant_message.insufficient_evidence_reason.replaceAll("_", " ")}.`
          : `${messageRun.turn.assistant_message.citations?.length ?? 0} durable citations returned.`
        : "Send a grounded message and inspect the persisted citation snapshots.",
      state: messageRun ? (messageRun.passed ? "passed" : "needs_attention") : "not_started",
      tab: "messages",
      action: "Open message test",
    },
    {
      title: "Refresh or change the corpus",
      detail: builds?.active_build_id
        ? `Active build is ${shortId(builds.active_build_id)}${builds.previous_build_id ? `; rollback target is ${shortId(builds.previous_build_id)}` : ""}.`
        : "Create and validate a build, then activate or roll it back.",
      state: lifecyclePassed
        ? "passed"
        : builds?.items.some((build) => build.state === "building")
          ? "in_progress"
          : "not_started",
      tab: "lifecycle",
      action: "Open lifecycle controls",
    },
  ];
  return (
    <section className="panel lab-journey">
      <div className="panel__heading">
        <div>
          <h2>End-to-end verification</h2>
          <p>Follow the sequence or jump directly to the check you need.</p>
        </div>
      </div>
      <ol className="journey-steps">
        {steps.map((step, index) => (
          <li key={step.title}>
            <span className={`journey-number journey-number--${step.state}`}>
              {step.state === "passed" ? <Check aria-hidden="true" /> : index + 1}
            </span>
            <div>
              <div className="journey-title">
                <h3>{step.title}</h3>
                <StatusBadge status={step.state} />
              </div>
              <p>{step.detail}</p>
            </div>
            <button
              className="button button--secondary"
              type="button"
              onClick={() => onNavigate(step.tab)}
            >
              {step.action} <ChevronRight size={15} aria-hidden="true" />
            </button>
          </li>
        ))}
      </ol>
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
  const lifecycle = useDocumentLifecycleAction(projectId);
  const [dragging, setDragging] = useState(false);
  const [purgeText, setPurgeText] = useState("");
  const [actionAccepted, setActionAccepted] = useState<{
    action: string;
    document: Document;
  } | null>(null);
  const selected = documents.find((document) => document.id === selectedId) ?? documents[0];
  const relatedJobs = jobs.filter((job) => job.document_id === selected?.id).slice(0, 5);
  const terminalJob = relatedJobs.find((job) => ["succeeded", "failed"].includes(job.state));

  const uploadFile = async (file?: File) => {
    if (!file) return;
    try {
      const document = await upload.mutateAsync({ file });
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
      const document = await lifecycle.mutateAsync({ documentId: selected.id, action });
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
            <h2>Upload and documents</h2>
            <p>
              PDF, DOCX, UTF-8 TXT/Markdown, PNG, JPEG, TIFF, or WebP. Maximum size follows
              deployment configuration.
            </p>
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
            void uploadFile(event.dataTransfer.files[0]);
          }}
        >
          <UploadCloud aria-hidden="true" />
          <strong>{upload.isPending ? "Uploading and submitting…" : "Drop a document here"}</strong>
          <span>or choose a file</span>
          <input
            type="file"
            disabled={upload.isPending}
            accept=".pdf,.docx,.txt,.md,.png,.jpg,.jpeg,.tif,.tiff,.webp"
            onChange={(event) => void uploadFile(event.target.files?.[0])}
          />
        </label>
        {upload.isSuccess && (
          <div className="notice-card lab-request-card" role="status">
            <Check aria-hidden="true" />
            <div>
              <strong>Request accepted</strong>
              <p>
                Document {upload.data.filename} was accepted. Processing is not complete until job{" "}
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
        {documents.length === 0 ? (
          <div className="inline-empty">No documents have been uploaded to this project.</div>
        ) : (
          <ul className="lab-document-list">
            {documents.map((document) => (
              <li key={document.id} className={document.id === selected?.id ? "selected" : ""}>
                <button type="button" onClick={() => onSelect(document.id)}>
                  <FileText aria-hidden="true" />
                  <span>
                    <strong>{document.filename}</strong>
                    <small>
                      v{document.version} · {formatBytes(document.size_bytes)}
                    </small>
                  </span>
                  <StatusBadge status={document.status} />
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section className="panel lab-document-detail">
        <div className="panel__heading">
          <div>
            <h2>{selected?.filename ?? "Select a document"}</h2>
            <p>
              {selected
                ? `Document ${shortId(selected.id)} · version ${selected.version}`
                : "Upload or select a document to inspect it."}
            </p>
          </div>
          {selected && <StatusBadge status={selected.status} />}
        </div>
        {selected ? (
          <div className="lab-panel-body">
            <dl className="detail-list">
              <div>
                <dt>Current status</dt>
                <dd>{selected.status}</dd>
              </div>
              <div>
                <dt>Processing job</dt>
                <dd>
                  {selected.job_id ? (
                    <Link to={`/jobs?project=${projectId}&job=${selected.job_id}`}>
                      {selected.job_id}
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
                <dt>Updated</dt>
                <dd>{formatDate(selected.updated_at)}</dd>
              </div>
            </dl>
            {selected.error_message && (
              <div className="failure-box">
                <strong>Processing failure</strong>
                <p>{selected.error_message}</p>
              </div>
            )}
            <section>
              <h3>Valid actions</h3>
              <div className="button-row">
                <ActionButton
                  label="Reprocess"
                  disabled={
                    lifecycle.isPending || ["deleting", "purging"].includes(selected.status)
                  }
                  reason="Unavailable while deletion or purge is running."
                  onClick={() => void runAction("reprocess")}
                />
                <ActionButton
                  label="Embed"
                  disabled={lifecycle.isPending || selected.status !== "chunked"}
                  reason="Embedding requires a chunked document."
                  onClick={() => void runAction("embed")}
                />
                <ActionButton
                  label="Index"
                  disabled={lifecycle.isPending || selected.status !== "embedded"}
                  reason="Indexing requires an embedded document."
                  onClick={() => void runAction("index")}
                />
                <ActionButton
                  label="Delete"
                  disabled={
                    lifecycle.isPending || ["deleting", "purging"].includes(selected.status)
                  }
                  reason="Delete is already running or the document is being purged."
                  onClick={() => void runAction("delete")}
                />
              </div>
              <p className="lab-help">
                Delete is reversible at the corpus level: retained artifacts remain available for
                rollback.
              </p>
            </section>
            <section className="lab-danger-zone">
              <h3>Purge permanently</h3>
              <p>
                Irreversibly removes relational and storage artifacts. Type{" "}
                <strong>{selected.filename}</strong> to continue.
              </p>
              <div className="lab-confirm-row">
                <input
                  aria-label="Purge confirmation"
                  value={purgeText}
                  onChange={(event) => setPurgeText(event.target.value)}
                  placeholder={selected.filename}
                />
                <button
                  className="danger-button"
                  type="button"
                  disabled={
                    lifecycle.isPending ||
                    purgeText !== selected.filename ||
                    selected.status === "purging"
                  }
                  onClick={() => void runAction("purge")}
                >
                  Purge
                </button>
              </div>
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
            {terminalJob && (
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
            <section>
              <h3>Related durable jobs</h3>
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
            </section>
          </div>
        ) : (
          <div className="inline-empty">No document selected.</div>
        )}
      </section>
    </div>
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
          <h2>Manual retrieval test</h2>
          <p>Run a focused query against the currently active immutable build.</p>
        </div>
      </div>
      <form className="lab-test-form" onSubmit={(event) => void submit(event)}>
        <label className="field-control field-control--grow">
          <span>Query</span>
          <input
            required
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="What phrase should this document contain?"
          />
        </label>
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
          {run.elapsedMs} ms client round trip · {run.response.diagnostics?.duration_ms ?? "—"} ms
          backend · active build {run.buildId ? shortId(run.buildId) : "none"}
        </span>
        {run.expected && (
          <span>
            Expected words “{run.expected}” were {run.passed ? "found" : "not found"}.
          </span>
        )}
      </div>
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
                  <strong>{result.filename}</strong>
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
              <small>
                Source: chars {result.char_start ?? "—"}–{result.char_end ?? "—"} · chunk{" "}
                {shortId(result.chunk_id)}
              </small>
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
    <section className="panel lab-focused-panel">
      <div className="panel__heading">
        <div>
          <h2>Grounded message test</h2>
          <p>One real conversation with regular or live-streamed grounded replies.</p>
        </div>
        <button
          className="button button--secondary"
          type="button"
          onClick={() => void newConversation()}
          disabled={create.isPending}
        >
          New test conversation
        </button>
      </div>
      {!hasActiveCorpus && (
        <div className="degraded-banner">
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
          <Menu aria-hidden="true" />
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
              <div className="lab-conversation-id">
                <span>Test conversation</span> <code>{shortId(conversationId)}</code>
              </div>
              <div ref={historyRef} className="message-history" aria-live="polite">
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
                      onInspect={
                        message.role === "assistant"
                          ? () => setSelectedAssistantId(message.id)
                          : undefined
                      }
                    />
                  ))
                ) : (
                  <p>No messages yet. Ask one focused validation question.</p>
                )}
                {stream.isPending && (
                  <article className="message-card message-card--assistant message-card--streaming">
                    <div>
                      <strong>Grounded response</strong>
                      <span className="lab-streaming-status">Streaming</span>
                    </div>
                    <p>{streamedContent || "Preparing grounded response…"}</p>
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
                  <button
                    className="lab-composer__send"
                    type="submit"
                    aria-label="Send message"
                    disabled={send.isPending || stream.isPending || !hasActiveCorpus}
                  >
                    <Send size={17} aria-hidden="true" />
                    <span>{send.isPending || stream.isPending ? "Sending" : "Send"}</span>
                  </button>
                  <div className="lab-composer__options">
                    <label className="lab-delivery-toggle">
                      <span>Reply mode</span>
                      <select
                        value={delivery}
                        onChange={(event) =>
                          setDelivery(event.target.value as "regular" | "stream")
                        }
                        disabled={send.isPending || stream.isPending}
                      >
                        <option value="regular">Regular</option>
                        <option value="stream">Stream live</option>
                      </select>
                    </label>
                    <label className="lab-expected-answer">
                      <span>Expected answer words</span>
                      <input
                        value={expected}
                        onChange={(event) => setExpected(event.target.value)}
                        placeholder="Optional check"
                      />
                    </label>
                    <span className="lab-composer__hint">
                      Enter sends · Shift + Enter adds a line
                    </span>
                  </div>
                </div>
              </form>
            </section>
            <MessageInspector message={inspectedMessage} run={lastRun} />
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
  onInspect,
}: {
  message: Message;
  selected?: boolean;
  onInspect?: () => void;
}) {
  const refusal = message.insufficient_evidence_reason;
  return (
    <article
      className={`message-card message-card--${message.role}${selected ? " message-card--selected" : ""}`}
    >
      <div>
        <strong>{message.role === "assistant" ? "Grounded response" : "Tester"}</strong>
        <time>{formatDate(message.created_at)}</time>
      </div>
      <p>{message.content}</p>
      {message.role === "assistant" && (
        <button className="message-card__inspect" type="button" onClick={onInspect}>
          {refusal
            ? "View refusal details"
            : `${message.citations?.length ?? 0} citation${message.citations?.length === 1 ? "" : "s"} · view evidence`}
        </button>
      )}
    </article>
  );
}

function MessageInspector({ message, run }: { message: Message | null; run: MessageRun | null }) {
  const isLatestRun = Boolean(run && message?.id === run.turn.assistant_message.id);
  if (!message) {
    return (
      <aside className="lab-message-inspector">
        <div className="lab-message-inspector__heading">
          <p className="eyebrow">Evidence inspector</p>
          <h3>Select an answer</h3>
        </div>
        <p>Grounding checks, response timing, and source citations appear here.</p>
      </aside>
    );
  }
  const refusal = message.insufficient_evidence_reason;
  const citations = message.citations ?? [];
  return (
    <aside className="lab-message-inspector" aria-label="Grounding details">
      <div className="lab-message-inspector__heading">
        <div>
          <p className="eyebrow">Evidence inspector</p>
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
      {refusal ? (
        <div className="notice-card">
          <strong>Insufficient evidence</strong>
          <p>{refusal.replaceAll("_", " ")}</p>
        </div>
      ) : citations.length ? (
        <ol className="citation-list" aria-label={`${citations.length} citations`}>
          {citations.slice(0, 5).map((citation, index) => (
            <li key={`${citation.chunk_id}-${index}`}>
              <strong>
                [{index + 1}] {citation.filename}
              </strong>
              <span>
                Page {citation.page_number ?? "—"} · chunk {citation.chunk_index} · score{" "}
                {citation.score.toFixed(4)}
              </span>
              <p>{citation.excerpt ?? `Stable chunk reference ${citation.chunk_id}`}</p>
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
        `${item.timestamp} | ${item.outcome} | ${item.name}${item.jobId ? ` | job ${item.jobId}` : ""}${item.code ? ` | ${item.code}` : ""}${item.traceId ? ` | trace ${item.traceId}` : ""}`,
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
                      {item.traceId && (
                        <div>
                          <dt>Trace ID</dt>
                          <dd>{item.traceId}</dd>
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
