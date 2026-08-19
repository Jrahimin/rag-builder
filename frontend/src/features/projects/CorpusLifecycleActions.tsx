import { useEffect, useRef, useState } from "react";
import { ArchiveRestore, CircleHelp, RefreshCw, ShieldCheck, X } from "lucide-react";
import { Link } from "react-router-dom";
import { OperatorApiError, type IndexBuild, type LifecycleJob } from "../../api/operatorApiClient";
import {
  useActivateIndexBuild,
  useCorpusLifecycleAction,
  useIndexBuilds,
  useJob,
} from "../../api/operatorConsoleQueries";
import { ErrorState, LoadingState } from "../../components/QueryStatePanel";
import { StatusBadge } from "../../components/StatusBadge";
import { formatDate, shortId } from "../../shared/formatters";
import { StructuredJobResult } from "../jobs/StructuredJobResult";

export type LifecycleNotice = {
  name: string;
  outcome: "accepted" | "running" | "passed" | "failed" | "warning";
  jobId?: string;
  buildId?: string;
  code?: string;
  requestId?: string | null;
  detail?: string;
  result?: Record<string, unknown> | null;
};

type LifecycleAction = "rebuild" | "reconcile" | "rollback";

function isLifecycleJob(value: LifecycleJob | IndexBuild): value is LifecycleJob {
  return "job_id" in value;
}

function LifecycleGuide({ onClose }: { onClose: () => void }) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <>
      <button
        className="lab-drawer-scrim"
        type="button"
        aria-label="Close lifecycle guide"
        onClick={onClose}
      />
      <section
        className="lifecycle-guide"
        role="dialog"
        aria-modal="true"
        aria-labelledby="lifecycle-guide-title"
      >
        <header>
          <div>
            <p className="eyebrow">Operator guide</p>
            <h2 id="lifecycle-guide-title">How corpus lifecycle works</h2>
          </div>
          <button className="icon-button" type="button" aria-label="Close" onClick={onClose}>
            <X size={16} aria-hidden="true" />
          </button>
        </header>
        <div className="lifecycle-guide__body">
          <p>
            Search and chat never read a live, half-updated index. They only read one frozen
            snapshot: the <strong>active build</strong>. New work is built privately, then you
            choose when it becomes searchable.
          </p>
          <h3>What a build contains</h3>
          <ul>
            <li>
              <strong>Chunks</strong> — text passages from ready documents.
            </li>
            <li>
              <strong>Vectors</strong> — embeddings used for meaning (semantic) search.
            </li>
            <li>
              <strong>Keywords</strong> — lexical index used for exact-word (BM25) search.
            </li>
          </ul>
          <p>
            Hybrid search fuses both. Counts like <code>7 / 7 / 7</code> mean that snapshot has 7 of
            each. <code>0 / 0 / 0</code> is an empty corpus at that moment (for example after
            purge), not a broken row.
          </p>
          <h3>States</h3>
          <dl>
            <div>
              <dt>Building</dt>
              <dd>Private write in progress. Not searchable.</dd>
            </div>
            <div>
              <dt>Validated</dt>
              <dd>Complete snapshot, still not live. Ready for Activate.</dd>
            </div>
            <div>
              <dt>Active</dt>
              <dd>The only snapshot search and chat use right now.</dd>
            </div>
            <div>
              <dt>Retained</dt>
              <dd>Kept after a later activation. The previous pointer is the rollback target.</dd>
            </div>
            <div>
              <dt>Failed</dt>
              <dd>Incomplete. Never activated.</dd>
            </div>
            <div>
              <dt>Superseded</dt>
              <dd>
                Cannot be activated (for example a build that still contains a purged document).
              </dd>
            </div>
          </dl>
          <h3>Top actions</h3>
          <dl>
            <div>
              <dt>Rebuild index</dt>
              <dd>
                Rebuild vectors and keywords from current chunks into a new private snapshot. Use
                after embedding-model, tokenizer, or FTS changes. Success means a validated snapshot
                — it is not live until Activate.
              </dd>
            </div>
            <div>
              <dt>Reconcile</dt>
              <dd>
                Compare database records with object storage. Read-only audit. Does not change the
                active index.
              </dd>
            </div>
            <div>
              <dt>Rollback</dt>
              <dd>
                Instantly make the previous retained build active. No rebuild. Use when a newly
                activated snapshot is worse.
              </dd>
            </div>
          </dl>
          <h3>Table columns</h3>
          <dl>
            <div>
              <dt>Operation</dt>
              <dd>
                Why this snapshot exists: <code>ingest</code> (upload finished), <code>delete</code>{" "}
                / <code>purge</code> (document removed from corpus), <code>reembed</code> (operator
                rebuild of vectors and keywords).
              </dd>
            </div>
            <div>
              <dt>Activate</dt>
              <dd>
                Swap the active pointer to this validated or retained snapshot. The current active
                row stays disabled because it is already live.
              </dd>
            </div>
          </dl>
          <h3>Typical sequence</h3>
          <ol>
            <li>
              Upload and process a document until Ready. Ingest usually creates and activates a
              build.
            </li>
            <li>
              Rebuild index when you need a new snapshot. Confirm, wait for the job, then Activate.
            </li>
            <li>
              Delete removes a document from the next active corpus but keeps artifacts for
              rollback.
            </li>
            <li>Purge does the same, then permanently deletes files, chunks, and embeddings.</li>
          </ol>
        </div>
      </section>
    </>
  );
}

export function CorpusLifecycleActions({
  projectId,
  onNotice,
}: {
  projectId: string;
  onNotice?: (notice: LifecycleNotice) => void;
}) {
  const builds = useIndexBuilds(projectId);
  const action = useCorpusLifecycleAction(projectId);
  const activate = useActivateIndexBuild(projectId);
  const [acceptedJob, setAcceptedJob] = useState("");
  const [acceptedBuild, setAcceptedBuild] = useState("");
  const [pendingConfirmation, setPendingConfirmation] = useState<LifecycleAction | null>(null);
  const [guideOpen, setGuideOpen] = useState(false);
  const notifiedTerminal = useRef("");
  const job = useJob(projectId, acceptedJob);

  useEffect(() => {
    if (!acceptedJob || !job.data || !["succeeded", "failed"].includes(job.data.state)) return;
    const notificationKey = `${job.data.id}:${job.data.state}`;
    if (notifiedTerminal.current === notificationKey) return;
    notifiedTerminal.current = notificationKey;
    onNotice?.({
      name: job.data.job_type.replaceAll(".", " "),
      outcome: job.data.state === "succeeded" ? "passed" : "failed",
      jobId: job.data.id,
      buildId: acceptedBuild || undefined,
      code: job.data.failure_code ?? undefined,
      detail: job.data.failure_message ?? `Job ${job.data.state}.`,
      result: job.data.result,
    });
  }, [acceptedBuild, acceptedJob, job.data, onNotice]);

  if (builds.isPending) return <LoadingState label="Loading index lifecycle" />;
  if (builds.isError)
    return <ErrorState error={builds.error} retry={() => void builds.refetch()} />;

  const active = builds.data.items.find((build) => build.id === builds.data.active_build_id);
  const previous = builds.data.items.find((build) => build.id === builds.data.previous_build_id);

  const run = async (name: LifecycleAction) => {
    setPendingConfirmation(null);
    try {
      const result = await action.mutateAsync(name);
      if (isLifecycleJob(result)) {
        setAcceptedJob(result.job_id);
        setAcceptedBuild(result.build_id ?? "");
        notifiedTerminal.current = "";
        onNotice?.({
          name: name === "reconcile" ? "Reconcile storage" : "Rebuild searchable index",
          outcome: "accepted",
          jobId: result.job_id,
          buildId: result.build_id ?? undefined,
          detail: result.created
            ? "Request accepted; waiting for the durable job."
            : "An existing idempotent job was returned; waiting for its terminal state.",
        });
      } else {
        onNotice?.({
          name: "Rollback active build",
          outcome: "passed",
          buildId: result.id,
          detail: `Active build is now ${shortId(result.id)}.`,
        });
      }
    } catch (error) {
      const typed = error instanceof OperatorApiError ? error : null;
      onNotice?.({
        name: name === "rollback" ? "Rollback active build" : name,
        outcome: "failed",
        code: typed?.code ?? "request_failed",
        requestId: typed?.requestId,
        detail: (error as Error).message,
      });
    }
  };

  const activateBuild = async (build: IndexBuild) => {
    try {
      const result = await activate.mutateAsync(build.id);
      onNotice?.({
        name: "Activate validated build",
        outcome: "passed",
        buildId: result.id,
        jobId: result.job_id ?? undefined,
        detail: `Active pointer changed to ${shortId(result.id)}.`,
      });
    } catch (error) {
      const typed = error instanceof OperatorApiError ? error : null;
      onNotice?.({
        name: "Activate validated build",
        outcome: "failed",
        buildId: build.id,
        code: typed?.code ?? "request_failed",
        requestId: typed?.requestId,
        detail: (error as Error).message,
      });
    }
  };

  const confirmationCopy: Record<LifecycleAction, string> = {
    rebuild:
      "Build a complete new vector and keyword snapshot from current chunks. The current active build stays searchable until you activate this one.",
    reconcile:
      "Compare database expectations with storage artifacts and return expected, actual, missing, orphan, and consistency facts.",
    rollback: previous
      ? `Make build ${previous.id} active. The current build ${active?.id ?? "none"} will become the rollback candidate.`
      : "No retained previous build is available.",
  };
  const mutationError = action.error ?? activate.error;

  return (
    <section className="panel corpus-lifecycle" aria-label="Corpus and index lifecycle">
      <div className="section-heading lifecycle-heading">
        <div>
          <p className="eyebrow">Safe corpus lifecycle</p>
          <div className="lifecycle-title-row">
            <h2>Immutable index builds</h2>
            <button
              className="icon-button"
              type="button"
              aria-label="Open lifecycle guide"
              title="How actions, states, and builds work"
              onClick={() => setGuideOpen(true)}
            >
              <CircleHelp size={18} aria-hidden="true" />
            </button>
          </div>
          <p>Validate a private build, deliberately activate it, and verify the active pointer.</p>
        </div>
        <div className="lifecycle-actions">
          <button
            className="lifecycle-action lifecycle-action--embed"
            type="button"
            disabled={action.isPending}
            onClick={() => setPendingConfirmation("rebuild")}
          >
            <RefreshCw aria-hidden="true" />
            <span>
              <strong>Rebuild index</strong>
              <small>Vectors and keywords</small>
            </span>
          </button>
          <button
            className="lifecycle-action lifecycle-action--reconcile"
            type="button"
            disabled={action.isPending}
            onClick={() => setPendingConfirmation("reconcile")}
          >
            <ShieldCheck aria-hidden="true" />
            <span>
              <strong>Reconcile</strong>
              <small>Check storage drift</small>
            </span>
          </button>
          <button
            className="lifecycle-action lifecycle-action--rollback"
            type="button"
            disabled={action.isPending || !previous}
            title={!previous ? "Rollback requires a retained previous build." : undefined}
            onClick={() => setPendingConfirmation("rollback")}
          >
            <ArchiveRestore aria-hidden="true" />
            <span>
              <strong>Rollback</strong>
              <small>Restore previous build</small>
            </span>
          </button>
        </div>
      </div>
      {guideOpen && <LifecycleGuide onClose={() => setGuideOpen(false)} />}
      <div className="build-pointer-grid">
        <article>
          <span>Active build</span>
          <strong>{active ? shortId(active.id) : "None"}</strong>
          <small>
            {active
              ? `${active.document_count} documents · ${active.chunk_count} chunks`
              : "Search has no active build."}
          </small>
        </article>
        <article>
          <span>Previous build</span>
          <strong>{previous ? shortId(previous.id) : "None"}</strong>
          <small>
            {previous
              ? `Rollback will make ${shortId(previous.id)} active.`
              : "No rollback target is retained."}
          </small>
        </article>
      </div>
      {pendingConfirmation && (
        <div
          className="lifecycle-confirmation"
          role="alertdialog"
          aria-label={`Confirm ${pendingConfirmation}`}
        >
          <div>
            <strong>Confirm {pendingConfirmation}</strong>
            <p>{confirmationCopy[pendingConfirmation]}</p>
          </div>
          <div className="button-row">
            <button
              className="button button--primary"
              type="button"
              onClick={() => void run(pendingConfirmation)}
            >
              Confirm action
            </button>
            <button
              className="button button--secondary"
              type="button"
              onClick={() => setPendingConfirmation(null)}
            >
              Cancel
            </button>
          </div>
        </div>
      )}
      {mutationError && (
        <div className="failure-box">
          <strong>{mutationError.message}</strong>
          {mutationError instanceof OperatorApiError && (
            <p>
              Code: {mutationError.code} · Request ID: {mutationError.requestId ?? "Not provided"}
            </p>
          )}
        </div>
      )}
      {acceptedJob && (
        <div className="lab-request-card notice-card">
          <div>
            <strong>Request accepted</strong>
            <p>
              Durable job{" "}
              <Link to={`/jobs?project=${projectId}&job=${acceptedJob}`}>{acceptedJob}</Link>{" "}
              {job.data ? `is ${job.data.state}` : "is loading"}. A successful response here does
              not mean processing finished.
            </p>
          </div>
        </div>
      )}
      {job.data?.failure_message && (
        <div className="failure-box">
          <strong>{job.data.failure_code ?? "Job failed"}</strong>
          <p>{job.data.failure_message}</p>
        </div>
      )}
      {job.data?.result && (
        <div className="lab-panel-body">
          <StructuredJobResult result={job.data.result} />
        </div>
      )}
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Build</th>
              <th>Operation</th>
              <th>State</th>
              <th>Documents</th>
              <th>Chunks / vectors / keywords</th>
              <th>Created / validated</th>
              <th>Validation</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            {builds.data.items.length === 0 && (
              <tr>
                <td colSpan={8}>
                  No index builds exist yet. Rebuild index to create an isolated build.
                </td>
              </tr>
            )}
            {builds.data.items.map((build) => {
              const canActivate = ["validated", "retained"].includes(build.state);
              return (
                <tr
                  key={build.id}
                  className={build.id === builds.data.active_build_id ? "row--selected" : ""}
                >
                  <td>
                    <strong>{shortId(build.id)}</strong>
                    {build.id === builds.data.active_build_id && <small>Active</small>}
                    {build.id === builds.data.previous_build_id && <small>Rollback target</small>}
                  </td>
                  <td>{build.operation}</td>
                  <td>
                    <StatusBadge status={build.state} />
                  </td>
                  <td>{build.document_count}</td>
                  <td>
                    {build.chunk_count} / {build.vector_count} / {build.keyword_count}
                  </td>
                  <td>
                    {formatDate(build.created_at)}
                    <small>
                      {build.validated_at
                        ? `Validated ${formatDate(build.validated_at)}`
                        : "Not validated"}
                    </small>
                  </td>
                  <td>
                    {build.failure_message ? (
                      <span className="lifecycle-failure">
                        <strong>{build.failure_code}</strong>
                        {build.failure_message}
                      </span>
                    ) : canActivate || build.state === "active" ? (
                      "Build is ready to activate"
                    ) : (
                      "Validation pending"
                    )}
                  </td>
                  <td>
                    <button
                      type="button"
                      disabled={activate.isPending || !canActivate}
                      title={
                        !canActivate
                          ? "Only validated or retained builds can be activated."
                          : undefined
                      }
                      onClick={() => void activateBuild(build)}
                    >
                      Activate
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
