import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Cpu, Database, HeartPulse } from "lucide-react";
import { operatorApiClient } from "../../api/operatorApiClient";
import { operatorQueryKeys } from "../../api/operatorConsoleQueries";
import { ErrorState, LoadingState } from "../../components/QueryStatePanel";
import { StatusBadge } from "../../components/StatusBadge";
import { formatDate } from "../../shared/formatters";

export function DependencyWorkerHealth() {
  const dependencies = useQuery({
    queryKey: operatorQueryKeys.dependencies,
    queryFn: operatorApiClient.getDependencies,
    refetchInterval: 10_000,
  });
  const workers = useQuery({
    queryKey: operatorQueryKeys.workers,
    queryFn: operatorApiClient.getWorkers,
    refetchInterval: 10_000,
  });
  if (dependencies.isPending || workers.isPending)
    return <LoadingState label="Checking system health" />;
  if (dependencies.isError)
    return <ErrorState error={dependencies.error} retry={() => void dependencies.refetch()} />;
  if (workers.isError)
    return <ErrorState error={workers.error} retry={() => void workers.refetch()} />;
  const healthyDependencies = dependencies.data.readiness.dependencies.filter(
    (item) => item.state === "ok",
  ).length;
  const totalDependencies = dependencies.data.readiness.dependencies.length;
  const isHealthy = dependencies.data.readiness.status === "ready" && workers.data.active_count > 0;
  return (
    <div className="page-stack">
      <section
        className={`health-hero ${isHealthy ? "health-hero--healthy" : "health-hero--degraded"}`}
      >
        <div>
          {isHealthy ? <CheckCircle2 aria-hidden="true" /> : <AlertTriangle aria-hidden="true" />}
          <div>
            <p>Overall system status</p>
            <h2>{isHealthy ? "Healthy" : "Degraded"}</h2>
            <span>
              {isHealthy
                ? "All required systems are operational"
                : "One or more operational checks need attention"}
            </span>
          </div>
        </div>
        <dl>
          <div>
            <dt>Dependencies</dt>
            <dd>
              {healthyDependencies} / {totalDependencies}
            </dd>
          </div>
          <div>
            <dt>Active workers</dt>
            <dd>{workers.data.active_count}</dd>
          </div>
          <div>
            <dt>Profile</dt>
            <dd>{dependencies.data.startup_profile}</dd>
          </div>
        </dl>
      </section>
      {!workers.data.available && (
        <section className="degraded-banner" role="alert">
          <AlertTriangle aria-hidden="true" />
          <div>
            <strong>Worker registry unavailable</strong>
            <p>{workers.data.detail}</p>
          </div>
        </section>
      )}
      <section className="panel">
        <div className="panel__heading">
          <div>
            <h2>Dependencies</h2>
            <p>Cached readiness checks safe for frequent polling</p>
          </div>
          <Database size={20} aria-hidden="true" />
        </div>
        <div className="table-scroll">
          <table>
            <caption className="sr-only">Dependency health checks</caption>
            <thead>
              <tr>
                <th>Dependency</th>
                <th>Status</th>
                <th>Latency</th>
                <th>Last check</th>
                <th>Detail / action</th>
              </tr>
            </thead>
            <tbody>
              {dependencies.data.readiness.dependencies.map((dependency) => (
                <tr key={dependency.name}>
                  <td>
                    <strong>{dependency.name}</strong>
                  </td>
                  <td>
                    <StatusBadge status={dependency.state} />
                  </td>
                  <td>
                    {dependency.latency_ms === null || dependency.latency_ms === undefined
                      ? "—"
                      : `${Math.round(dependency.latency_ms)} ms`}
                  </td>
                  <td>{formatDate(dependency.checked_at)}</td>
                  <td>
                    {dependency.detail ?? dependency.action ?? "—"}
                    {dependency.cached && <span className="cached-label"> cached</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <section className="panel">
        <div className="panel__heading">
          <div>
            <h2>Worker pool</h2>
            <p>Expiring Redis heartbeats · stale after {workers.data.stale_after_seconds}s</p>
          </div>
          <Cpu size={20} aria-hidden="true" />
        </div>
        {workers.data.workers.length === 0 ? (
          <div className="inline-empty">
            <HeartPulse size={18} aria-hidden="true" /> No worker heartbeats are currently
            registered.
          </div>
        ) : (
          <div className="table-scroll">
            <table>
              <caption className="sr-only">Worker pool</caption>
              <thead>
                <tr>
                  <th>Worker</th>
                  <th>Status</th>
                  <th>Host</th>
                  <th>Queue</th>
                  <th>Heartbeat age</th>
                  <th>Version</th>
                </tr>
              </thead>
              <tbody>
                {workers.data.workers.map((worker) => (
                  <tr key={worker.worker_id}>
                    <td>
                      <strong>{worker.worker_id}</strong>
                    </td>
                    <td>
                      <StatusBadge status={worker.state} />
                    </td>
                    <td>
                      {worker.hostname} · PID {worker.process_id}
                    </td>
                    <td>{worker.queue}</td>
                    <td>{worker.heartbeat_age_seconds.toFixed(1)}s</td>
                    <td>{worker.version}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
