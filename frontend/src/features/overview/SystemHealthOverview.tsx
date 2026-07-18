import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, BriefcaseBusiness, Database, FileStack, HeartPulse } from "lucide-react";
import { overviewQueryOptions } from "../../api/operatorConsoleQueries";
import { ErrorState, LoadingState } from "../../components/QueryStatePanel";
import { MetricCard } from "../../components/MetricCard";
import { StatusBadge } from "../../components/StatusBadge";
import { formatBytes, formatDate, formatNumber, shortId } from "../../shared/formatters";

export function SystemHealthOverview() {
  const overview = useQuery(overviewQueryOptions);
  if (overview.isPending) return <LoadingState label="Loading deployment overview" />;
  if (overview.isError)
    return <ErrorState error={overview.error} retry={() => void overview.refetch()} />;

  const { metrics, dependencies, workers } = overview.data;
  const failures = overview.data.recent_failures ?? [];
  const documentCount = metrics.corpus.documents;
  return (
    <div className="page-stack">
      {overview.data.status !== "ready" && (
        <section className="degraded-banner" role="status">
          <AlertTriangle aria-hidden="true" />
          <div>
            <strong>Deployment is degraded</strong>
            <p>Review dependency checks and worker availability below.</p>
          </div>
        </section>
      )}

      <section className="metric-grid" aria-label="Deployment summary">
        <MetricCard
          label="Active jobs"
          value={metrics.jobs.queued + metrics.jobs.running + metrics.jobs.retry_scheduled}
          detail={`${metrics.jobs.running} running · ${metrics.jobs.queued} queued`}
          icon={BriefcaseBusiness}
        />
        <MetricCard
          label="Failures (24h)"
          value={metrics.jobs.failures_24h}
          detail={`${metrics.jobs.retry_attempts} retry attempts overall`}
          icon={AlertTriangle}
          tone={metrics.jobs.failures_24h ? "red" : "green"}
        />
        <MetricCard
          label="Documents"
          value={formatNumber(documentCount)}
          detail={`${formatNumber(metrics.corpus.chunks)} chunks`}
          icon={FileStack}
        />
        <MetricCard
          label="System state"
          value={overview.data.status === "ready" ? "Healthy" : "Degraded"}
          detail={`${workers.active_count} active workers`}
          icon={HeartPulse}
          tone={overview.data.status === "ready" ? "green" : "amber"}
        />
      </section>

      <div className="split-grid">
        <section className="panel">
          <div className="panel__heading">
            <div>
              <h2>Recent failures</h2>
              <p>Latest durable jobs requiring attention</p>
            </div>
          </div>
          {failures.length === 0 ? (
            <div className="inline-empty">
              <HeartPulse size={18} aria-hidden="true" /> No recent job failures.
            </div>
          ) : (
            <ul className="event-list">
              {failures.map((failure) => (
                <li key={failure.job_id}>
                  <span className="event-icon event-icon--danger">
                    <AlertTriangle size={15} aria-hidden="true" />
                  </span>
                  <div>
                    <strong>Job {shortId(failure.job_id)}</strong>
                    <span>{failure.failure_message}</span>
                  </div>
                  <time dateTime={failure.failed_at}>{formatDate(failure.failed_at)}</time>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="panel">
          <div className="panel__heading">
            <div>
              <h2>Queue state</h2>
              <p>Durable execution and dispatch pressure</p>
            </div>
          </div>
          <dl className="definition-grid definition-grid--compact">
            <div>
              <dt>Queued</dt>
              <dd>{metrics.jobs.queued}</dd>
            </div>
            <div>
              <dt>Running</dt>
              <dd>{metrics.jobs.running}</dd>
            </div>
            <div>
              <dt>Retry scheduled</dt>
              <dd>{metrics.jobs.retry_scheduled}</dd>
            </div>
            <div>
              <dt>Pending dispatches</dt>
              <dd>{metrics.jobs.pending_dispatches}</dd>
            </div>
            <div>
              <dt>Oldest queue age</dt>
              <dd>
                {metrics.jobs.oldest_queue_age_seconds
                  ? `${Math.round(metrics.jobs.oldest_queue_age_seconds)}s`
                  : "—"}
              </dd>
            </div>
            <div>
              <dt>Corpus storage</dt>
              <dd>{formatBytes(metrics.corpus.storage_bytes)}</dd>
            </div>
          </dl>
        </section>
      </div>

      <section className="panel">
        <div className="panel__heading">
          <div>
            <h2>Dependency summary</h2>
            <p>Readiness checks from the backend</p>
          </div>
          <StatusBadge status={dependencies.readiness.status} />
        </div>
        <div className="dependency-strip">
          {dependencies.readiness.dependencies.map((dependency) => (
            <article key={dependency.name}>
              <Database size={16} aria-hidden="true" />
              <strong>{dependency.name}</strong>
              <StatusBadge status={dependency.state} />
              <span>
                {dependency.latency_ms === null || dependency.latency_ms === undefined
                  ? "No latency"
                  : `${Math.round(dependency.latency_ms)} ms`}
              </span>
            </article>
          ))}
          <article>
            <BriefcaseBusiness size={16} aria-hidden="true" />
            <strong>Workers</strong>
            <StatusBadge status={workers.active_count > 0 ? "healthy" : "unavailable"} />
            <span>{workers.active_count} active</span>
          </article>
        </div>
      </section>
    </div>
  );
}
