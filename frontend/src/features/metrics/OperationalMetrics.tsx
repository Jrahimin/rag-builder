import { useQuery } from "@tanstack/react-query";
import { Activity, Boxes, Clock3, Database, Gauge, Sigma } from "lucide-react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { operatorApiClient } from "../../api/operatorApiClient";
import { operatorQueryKeys } from "../../api/operatorConsoleQueries";
import { ErrorState, LoadingState } from "../../components/QueryStatePanel";
import { MetricCard } from "../../components/MetricCard";
import { formatBytes, formatDuration, formatNumber } from "../../shared/formatters";

export function OperationalMetrics() {
  const metrics = useQuery({
    queryKey: operatorQueryKeys.metrics,
    queryFn: operatorApiClient.getMetrics,
    refetchInterval: 15_000,
  });
  if (metrics.isPending) return <LoadingState label="Loading operational metrics" />;
  if (metrics.isError)
    return <ErrorState error={metrics.error} retry={() => void metrics.refetch()} />;
  const data = metrics.data;
  const chart = data.job_latency.map((metric) => ({
    name: metric.name.replace("document.", ""),
    average: metric.average_ms ?? 0,
    maximum: metric.maximum_ms ?? 0,
  }));
  return (
    <div className="page-stack">
      <section className="metric-grid">
        <MetricCard
          label="Jobs"
          value={formatNumber(data.jobs.total)}
          detail={`${data.jobs.failures_24h} failures in 24h`}
          icon={Gauge}
        />
        <MetricCard
          label="Token usage"
          value={formatNumber(data.token_usage.total_tokens)}
          detail={`${formatNumber(data.token_usage.input_tokens)} input · ${formatNumber(data.token_usage.output_tokens)} output`}
          icon={Sigma}
        />
        <MetricCard
          label="Corpus"
          value={formatNumber(data.corpus.documents)}
          detail={`${formatNumber(data.corpus.chunks)} chunks`}
          icon={Database}
        />
        <MetricCard
          label="Storage"
          value={formatBytes(data.corpus.storage_bytes)}
          detail={`${data.corpus.projects} projects · index v${data.active_embedding_set_version}`}
          icon={Boxes}
        />
      </section>
      <div className="split-grid">
        <section className="panel">
          <div className="panel__heading">
            <div>
              <h2>Job latency</h2>
              <p>Average and maximum completed-run duration</p>
            </div>
          </div>
          {chart.length === 0 ? (
            <div className="inline-empty">
              <Clock3 size={18} aria-hidden="true" /> No completed job latency samples yet.
            </div>
          ) : (
            <div className="chart-container" aria-label="Job latency chart">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={chart} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e2e8f0" />
                  <XAxis dataKey="name" tickLine={false} axisLine={false} />
                  <YAxis tickLine={false} axisLine={false} unit="ms" width={58} />
                  <Tooltip formatter={(value) => `${Number(value).toFixed(0)} ms`} />
                  <Bar dataKey="average" fill="#2f6feb" radius={[4, 4, 0, 0]} />
                  <Bar dataKey="maximum" fill="#9db8f3" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </section>
        <section className="panel">
          <div className="panel__heading">
            <div>
              <h2>Request latency</h2>
              <p>RAG interaction timing</p>
            </div>
          </div>
          <dl className="latency-list">
            <div>
              <dt>Retrieval average</dt>
              <dd>{formatDuration(data.retrieval_latency.average_ms)}</dd>
              <span>{data.retrieval_latency.count} samples</span>
            </div>
            <div>
              <dt>Retrieval maximum</dt>
              <dd>{formatDuration(data.retrieval_latency.maximum_ms)}</dd>
            </div>
            <div>
              <dt>Generation average</dt>
              <dd>{formatDuration(data.generation_latency.average_ms)}</dd>
              <span>{data.generation_latency.count} samples</span>
            </div>
            <div>
              <dt>Generation maximum</dt>
              <dd>{formatDuration(data.generation_latency.maximum_ms)}</dd>
            </div>
          </dl>
        </section>
      </div>
      <section className="panel">
        <div className="panel__heading">
          <div>
            <h2>Queue and dispatch</h2>
            <p>Current durable work pressure</p>
          </div>
          <Activity size={20} aria-hidden="true" />
        </div>
        <dl className="configuration-grid">
          <div>
            <dt>Queued</dt>
            <dd>{data.jobs.queued}</dd>
          </div>
          <div>
            <dt>Running</dt>
            <dd>{data.jobs.running}</dd>
          </div>
          <div>
            <dt>Retry scheduled</dt>
            <dd>{data.jobs.retry_scheduled}</dd>
          </div>
          <div>
            <dt>Pending dispatches</dt>
            <dd>{data.jobs.pending_dispatches}</dd>
          </div>
          <div>
            <dt>Dispatch attempts</dt>
            <dd>{data.jobs.dispatch_attempts}</dd>
          </div>
          <div>
            <dt>Oldest dispatch age</dt>
            <dd>
              {data.jobs.oldest_dispatch_age_seconds
                ? `${Math.round(data.jobs.oldest_dispatch_age_seconds)}s`
                : "—"}
            </dd>
          </div>
        </dl>
      </section>
    </div>
  );
}
