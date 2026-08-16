import { useQuery } from "@tanstack/react-query";
import { Activity, Boxes, Clock3, Database, Gauge, Sigma } from "lucide-react";
import { useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import {
  operatorApiClient,
  type UsageBucket,
  type UsageFilters,
  type UsageWorkload,
} from "../../api/operatorApiClient";
import { operatorQueryKeys } from "../../api/operatorConsoleQueries";
import { ErrorState, LoadingState } from "../../components/QueryStatePanel";
import { MetricCard } from "../../components/MetricCard";
import { formatBytes, formatDuration, formatNumber } from "../../shared/formatters";

export function OperationalMetrics() {
  const [bucket, setBucket] = useState<UsageBucket>("day");
  const [workload, setWorkload] = useState<UsageWorkload | "">("");
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const usageFilters: UsageFilters = {
    bucket,
    workload: workload || undefined,
    provider: provider || undefined,
    model: model || undefined,
  };
  const metrics = useQuery({
    queryKey: operatorQueryKeys.metrics,
    queryFn: operatorApiClient.getMetrics,
    refetchInterval: 15_000,
  });
  const usage = useQuery({
    queryKey: operatorQueryKeys.usage(usageFilters),
    queryFn: () => operatorApiClient.getUsage(usageFilters),
    refetchInterval: 30_000,
  });
  if (metrics.isPending || usage.isPending)
    return <LoadingState label="Loading operational metrics" />;
  if (metrics.isError)
    return <ErrorState error={metrics.error} retry={() => void metrics.refetch()} />;
  if (usage.isError) return <ErrorState error={usage.error} retry={() => void usage.refetch()} />;
  const data = metrics.data;
  const usageData = usage.data;
  const chart = data.job_latency.map((metric) => ({
    name: shortJobName(metric.name),
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
          value={formatNullableNumber(usageData.totals.total_tokens)}
          detail={`${usageData.totals.records_with_token_usage}/${usageData.totals.request_count} requests reported usage`}
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
            <div className="chart-legend" aria-hidden="true">
              <span>
                <i className="chart-legend__swatch chart-legend__swatch--average" /> Average
              </span>
              <span>
                <i className="chart-legend__swatch chart-legend__swatch--maximum" /> Maximum
              </span>
            </div>
          </div>
          {chart.length === 0 ? (
            <div className="inline-empty">
              <Clock3 size={18} aria-hidden="true" /> No completed job latency samples yet.
            </div>
          ) : (
            <div className="chart-container" aria-label="Job latency chart">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={chart} margin={{ top: 8, right: 12, left: 4, bottom: 28 }}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e2e8f0" />
                  <XAxis
                    dataKey="name"
                    tickLine={false}
                    axisLine={false}
                    interval={0}
                    angle={-28}
                    textAnchor="end"
                    height={56}
                    tick={{ fontSize: 11 }}
                  />
                  <YAxis
                    tickLine={false}
                    axisLine={false}
                    width={44}
                    tickFormatter={formatAxisMs}
                    tick={{ fontSize: 11 }}
                  />
                  <Tooltip
                    formatter={(value, name) => [
                      formatDuration(Number(value)),
                      name === "average" ? "Average" : "Maximum",
                    ]}
                  />
                  <Bar dataKey="average" name="average" fill="#2f6feb" radius={[4, 4, 0, 0]} />
                  <Bar dataKey="maximum" name="maximum" fill="#9db8f3" radius={[4, 4, 0, 0]} />
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
            <h2>Execution usage</h2>
            <p>
              Organization, Project, provider, model, workload, and time aggregates. Missing
              provider usage remains unknown.
            </p>
          </div>
        </div>
        <div className="usage-toolbar" aria-label="Usage filters">
          <label className="field-control">
            <span>Bucket</span>
            <select
              value={bucket}
              onChange={(event) => setBucket(event.target.value as UsageBucket)}
            >
              <option value="hour">Hour</option>
              <option value="day">Day</option>
              <option value="month">Month</option>
            </select>
          </label>
          <label className="field-control">
            <span>Workload</span>
            <select
              value={workload}
              onChange={(event) => setWorkload(event.target.value as UsageWorkload | "")}
            >
              <option value="">All workloads</option>
              <option value="chat">Chat</option>
              <option value="contextual_generation">Contextual generation</option>
              <option value="evaluation">Evaluation</option>
            </select>
          </label>
          <label className="field-control">
            <span>Provider</span>
            <input
              value={provider}
              onChange={(event) => setProvider(event.target.value)}
              placeholder="All providers"
            />
          </label>
          <label className="field-control">
            <span>Model</span>
            <input
              value={model}
              onChange={(event) => setModel(event.target.value)}
              placeholder="All models"
            />
          </label>
        </div>
        <dl className="configuration-grid usage-summary">
          <div>
            <dt>Requests</dt>
            <dd>{formatNumber(usageData.totals.request_count)}</dd>
          </div>
          <div>
            <dt>Errors</dt>
            <dd>{formatNumber(usageData.totals.error_count)}</dd>
          </div>
          <div>
            <dt>Input tokens</dt>
            <dd>{formatNullableNumber(usageData.totals.input_tokens)}</dd>
          </div>
          <div>
            <dt>Output tokens</dt>
            <dd>{formatNullableNumber(usageData.totals.output_tokens)}</dd>
          </div>
          <div>
            <dt>Provider average</dt>
            <dd>{formatDuration(usageData.totals.provider_latency.average_ms)}</dd>
          </div>
          <div>
            <dt>Total average</dt>
            <dd>{formatDuration(usageData.totals.total_latency.average_ms)}</dd>
          </div>
        </dl>
        {usageData.items.length === 0 ? (
          <div className="inline-empty">No executions matched the selected usage filters.</div>
        ) : (
          <div className="table-scroll usage-table">
            <table>
              <thead>
                <tr>
                  <th>Period</th>
                  <th>Organization / Project</th>
                  <th>Provider / Model</th>
                  <th>Workload</th>
                  <th>Requests</th>
                  <th>Tokens</th>
                  <th>Provider latency</th>
                  <th>Total latency</th>
                </tr>
              </thead>
              <tbody>
                {usageData.items.map((item, index) => (
                  <tr
                    key={`${item.bucket_start ?? "total"}-${item.project_id ?? "unknown"}-${item.provider ?? "unknown"}-${item.model ?? "unknown"}-${item.workload ?? "unknown"}-${index}`}
                  >
                    <td>{formatPeriod(item.bucket_start)}</td>
                    <td>
                      <strong>{item.organization_name ?? "Unknown organization"}</strong>
                      <small>{item.project_name ?? "Unknown Project"}</small>
                    </td>
                    <td>
                      <strong>{item.provider ?? "Unknown provider"}</strong>
                      <small>{item.model ?? "Unknown model"}</small>
                    </td>
                    <td>{formatWorkload(item.workload)}</td>
                    <td>
                      {formatNumber(item.request_count)}
                      {item.error_count ? ` · ${item.error_count} errors` : ""}
                    </td>
                    <td>
                      {formatNullableNumber(item.total_tokens)}
                      <small>
                        {item.records_with_token_usage}/{item.request_count} reported
                      </small>
                    </td>
                    <td>{formatDuration(item.provider_latency.average_ms)}</td>
                    <td>{formatDuration(item.total_latency.average_ms)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
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

function shortJobName(name: string) {
  return name.replace(/^(document|corpus|index|plus)\./, "");
}

function formatAxisMs(value: number) {
  if (value >= 60_000) return `${Math.round(value / 60_000)}m`;
  if (value >= 1000) return `${Math.round(value / 1000)}s`;
  return `${Math.round(value)}`;
}

function formatNullableNumber(value: number | null) {
  return value === null ? "Unknown" : formatNumber(value);
}

function formatPeriod(value?: string | null) {
  if (!value) return "—";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(new Date(value));
}

function formatWorkload(value?: string | null) {
  return value ? value.replaceAll("_", " ") : "Unknown";
}
