import { useEffect, useRef, useState } from "react";
import { ArchiveRestore, DatabaseZap, RefreshCw, ShieldCheck } from "lucide-react";
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

type LifecycleAction = "reembed" | "reindex" | "reconcile" | "rollback";

function isLifecycleJob(value: LifecycleJob | IndexBuild): value is LifecycleJob {
  return "job_id" in value;
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
          name:
            name === "reconcile"
              ? "Reconcile storage"
              : name === "reembed"
                ? "Re-embed whole corpus"
                : "Reindex whole corpus",
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
    reembed:
      "Build a complete new vector and keyword snapshot. The current active build stays available until activation.",
    reindex:
      "Build a new isolated corpus snapshot. Success means the build is validated, not active.",
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
          <h2>Immutable index builds</h2>
          <p>Validate a private build, deliberately activate it, and verify the active pointer.</p>
        </div>
        <div className="lifecycle-actions">
          <button
            className="lifecycle-action lifecycle-action--embed"
            type="button"
            disabled={action.isPending}
            onClick={() => setPendingConfirmation("reembed")}
          >
            <RefreshCw aria-hidden="true" />
            <span>
              <strong>Re-embed</strong>
              <small>Refresh vectors</small>
            </span>
          </button>
          <button
            className="lifecycle-action lifecycle-action--index"
            type="button"
            disabled={action.isPending}
            onClick={() => setPendingConfirmation("reindex")}
          >
            <DatabaseZap aria-hidden="true" />
            <span>
              <strong>Reindex</strong>
              <small>Build a new snapshot</small>
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
                <td colSpan={8}>No index builds exist yet. Reindex to create an isolated build.</td>
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
