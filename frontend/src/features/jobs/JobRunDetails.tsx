import { RefreshCw, X } from "lucide-react";
import { useJob, useRetryJob } from "../../api/operatorConsoleQueries";
import { ErrorState, LoadingState } from "../../components/QueryStatePanel";
import { StatusBadge } from "../../components/StatusBadge";
import { formatDate } from "../../shared/formatters";
import { StructuredJobResult } from "./StructuredJobResult";

export function JobRunDetails({
  projectId,
  jobId,
  onClose,
}: {
  projectId: string;
  jobId: string;
  onClose: () => void;
}) {
  const job = useJob(projectId, jobId);
  const retry = useRetryJob(projectId);
  return (
    <aside className="detail-panel" aria-label="Job run details">
      <div className="detail-panel__header">
        <div>
          <p className="eyebrow">Job run</p>
          <h2>{jobId.slice(0, 8)}</h2>
        </div>
        <button
          className="icon-button"
          type="button"
          onClick={onClose}
          aria-label="Close job details"
        >
          <X aria-hidden="true" />
        </button>
      </div>
      {job.isPending ? (
        <LoadingState label="Loading job details" />
      ) : job.isError ? (
        <ErrorState error={job.error} retry={() => void job.refetch()} />
      ) : (
        <div className="detail-panel__body">
          {retry.isSuccess && (
            <div className="success-note" role="status">
              Retry queued as job {retry.data.id.slice(0, 8)}.
            </div>
          )}
          {retry.isError && (
            <div className="error-note" role="alert">
              {retry.error.message}
            </div>
          )}
          <StatusBadge status={job.data.state} />
          <dl className="detail-list">
            <div>
              <dt>Type</dt>
              <dd>{job.data.job_type}</dd>
            </div>
            <div>
              <dt>Stage</dt>
              <dd>{job.data.stage}</dd>
            </div>
            <div>
              <dt>Progress</dt>
              <dd>{job.data.progress}%</dd>
            </div>
            <div>
              <dt>Attempts</dt>
              <dd>
                {job.data.attempt_count} / {job.data.max_attempts}
              </dd>
            </div>
            <div>
              <dt>Document</dt>
              <dd>{job.data.document_id?.slice(0, 8) ?? "—"}</dd>
            </div>
            <div>
              <dt>Queued</dt>
              <dd>{formatDate(job.data.queued_at)}</dd>
            </div>
            <div>
              <dt>Started</dt>
              <dd>{formatDate(job.data.started_at)}</dd>
            </div>
            <div>
              <dt>Completed</dt>
              <dd>{formatDate(job.data.completed_at)}</dd>
            </div>
            <div>
              <dt>Configuration</dt>
              <dd>
                <code>{job.data.configuration_hash.slice(0, 12)}</code>
              </dd>
            </div>
          </dl>
          <div className="progress-track" aria-label={`${job.data.progress}% complete`}>
            <span style={{ width: `${job.data.progress}%` }} />
          </div>
          {job.data.failure_message && (
            <section className="failure-box">
              <h3>{job.data.failure_code ?? "Job failed"}</h3>
              <p>{job.data.failure_message}</p>
            </section>
          )}
          {job.data.result && <StructuredJobResult result={job.data.result} />}
          <section>
            <h3>Configuration snapshot</h3>
            <details>
              <summary>Technical configuration</summary>
              <pre className="json-view">{JSON.stringify(job.data.configuration, null, 2)}</pre>
            </details>
          </section>
          <button
            className="button button--primary button--full"
            type="button"
            disabled={job.data.state !== "failed" || retry.isPending}
            onClick={() => retry.mutate(job.data.id)}
          >
            <RefreshCw size={16} aria-hidden="true" />{" "}
            {retry.isPending ? "Queuing retry…" : "Retry failed job"}
          </button>
          {job.data.state !== "failed" && (
            <p className="helper-text">
              Retry is available only after a job reaches the failed state.
            </p>
          )}
        </div>
      )}
    </aside>
  );
}
